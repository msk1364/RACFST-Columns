import ctypes
import sys
import threading
import traceback
from pathlib import Path
from datetime import datetime

import joblib
import numpy as np
import pandas as pd

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText


# =========================================================
# High-DPI Awareness (must run BEFORE tk.Tk() is created)
# =========================================================
def _enable_high_dpi_awareness():
    """
    فعال‌سازی Per-Monitor V2 DPI awareness تا ویندوز تصویر Tkinter را
    Blurry نکند. رندر مستقیم روی رزولوشن بومی مانیتور انجام می‌شود.
    """
    if sys.platform != "win32":
        return
    try:  # Windows 10 v1703+
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        return
    except Exception:
        pass
    try:  # Windows 8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:  # Vista+
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _get_monitor_dpi():
    """DPI مؤثر مانیتور اصلی (مثلاً 96 / 120 / 144 / 192)."""
    if sys.platform != "win32":
        return 96
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        LOGPIXELSX = 88
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi > 0:
            return dpi
    except Exception:
        pass
    return 96


# ⚠️ اجبار DPI به مقدار خاص:
# اگر واقعاً بخواهی خودت DPI را دستی override کنی عدد بگذار
# (مثلاً 600).  مقدار None یعنی از DPI واقعی مانیتور استفاده کن
# که رفتار درست و خوانا است.  600 عملاً مانیتور را غول‌پیکر می‌کند.
DPI_OVERRIDE = None

_enable_high_dpi_awareness()


# =========================================================
# Configuration
# =========================================================
APP_DIR = Path(__file__).resolve().parent
TARGET_COL = "Nu_Kn"

MODEL_FILES = {}

BG          = "#F5F7FA"
PANEL       = "#FFFFFF"
PRIMARY     = "#2563EB"
PRIMARY_DK  = "#1D4ED8"
SUCCESS     = "#16A34A"
SUCCESS_DK  = "#15803D"
DANGER      = "#DC2626"
WARNING     = "#EA580C"
TEXT        = "#1F2937"
MUTED       = "#6B7280"
BORDER      = "#D1D5DB"
ACCENT_BG   = "#EFF6FF"


# =========================================================
# Canonical column matching  (ξ replaces X / ꭓ)
# =========================================================
CANONICAL_GROUPS = {
    "D":       ["D", "d", "dia", "diameter", "diam", "D (mm)", "d (mm)"],
    "L":       ["L", "l", "len", "length", "L (mm)", "l (mm)"],
    "t":       ["t", "thk", "thickness", "t (mm)", "wall_thickness"],
    "fc":      ["fc", "f'c", "fck", "fcu", "fc (MPa)", "fcm"],
    "Ec":      ["Ec", "E", "e", "Ec (MPa)", "E (MPa)", "emodulus"],
    "r":       ["r", "r (%)", "r(%)", "rho", "ρ", "ratio", "reinforcement_ratio"],
    "fy":      ["fy", "f_y", "fyy", "fy (MPa)", "yield"],
    "Es":      ["Es", "E_s", "Es (MPa)", "steel_modulus"],
    "D/t":     ["D/t", "D_t", "DoverT", "D over t", "Dt"],
    "L/D":     ["L/D", "L_D", "LoverD", "L over D", "LD"],
    "Npl":     ["Npl", "N_pl", "NPL", "N_pl_"],
    "ξ":       ["ξ", "X", "x", "χ", "ꭓ", "chi", "Chi", "xi", "Xi",
                "X_chi", "χ_", "ξ_"],
    "S_TCS":   ["S_TCS", "S.T_CS", "S_T_CS", "S.T._CS", "STCS", "S_TC_S",
                "S_TCS_", "S.TCS"],
    "S_T_S":   ["S_T_S", "S.T_SS", "S_T_SS", "S.T._SS", "STSS", "S_TSS",
                "S_T_SS_", "S.T.SS"],
}

FEATURE_UNITS = {
    "D":       "mm",
    "L":       "mm",
    "t":       "mm",
    "fc":      "MPa",
    "Ec":      "MPa",
    "r":       "%",
    "fy":      "MPa",
    "Es":      "MPa",
    "D/t":     "-",
    "L/D":     "-",
    "Npl":     "kN",
    "ξ":       "-",
    "S_TCS":   "-",
    "S_T_S":   "-",
}

# =========================================================
# Valid ranges for features (from the user-provided table).
# Only these canonical features are subject to range validation.
# =========================================================
FEATURE_RANGES = {
    "D":   (88.02,    256.00),
    "L":   (283.0,    1800.0),
    "t":   (1.50,     6.57),
    "fc":  (16.08,    68.20),
    "Ec":  (18846.9,  261000.0),
    "r":   (0.0,      1.0),
    "fy":  (176.50,   721.40),
    "Es":  (179000.0, 226800.0),
    "D/t": (20.00,    150.70),
    "L/D": (2.007,    11.857),
    "Npl": (356.57,   4018.35),
    "ξ":   (0.20,     3.27),
}

DERIVED_FEATURES   = {"D/t", "L/D", "Npl", "ξ"}
SKIN_TYPE_FEATURES = {"S_TCS", "S_T_S"}
SKIN_TYPE_OPTIONS  = ["Stainless Steel", "Carbon Steel"]
SKIN_TYPE_MAP = {
    "Stainless Steel": {"S_TCS": 0, "S_T_S": 1},
    "Carbon Steel":    {"S_TCS": 1, "S_T_S": 0},
}


def _norm(s):
    s = str(s).strip()
    for ch in [" ", ".", "-", "_", "%", "(", ")", "（", "）", "[", "]"]:
        s = s.replace(ch, "")
    return s.lower()


_ALIAS_TO_CANON = {}
for _canon, _variants in CANONICAL_GROUPS.items():
    for _v in _variants:
        _ALIAS_TO_CANON[_norm(_v)] = _canon
    _ALIAS_TO_CANON[_norm(_canon)] = _canon


def canonical_of(name):
    return _ALIAS_TO_CANON.get(_norm(name))


def unit_of(name):
    canon = canonical_of(name)
    if canon is not None and canon in FEATURE_UNITS:
        return FEATURE_UNITS[canon]
    return None


# =========================================================
# Range validation helpers
# =========================================================
def _check_value_or_reason(canon, value):
    """
    اگر فیچر بازه‌ای تعریف‌شده نداشته باشد یا مقدار قابل تبدیل به عدد
    نباشد، (True, "") برمی‌گرداند. در غیر اینصورت اگر خارج از بازه
    باشد (False, reason) برمی‌گرداند.
    """
    if canon is None or canon not in FEATURE_RANGES:
        return True, ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return True, ""
    try:
        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            return True, ""
    except Exception:
        pass
    lo, hi = FEATURE_RANGES[canon]
    if v < lo:
        return False, f"{v:g} < min ({lo:g})"
    if v > hi:
        return False, f"{v:g} > max ({hi:g})"
    return True, ""


def _find_dataset_range_problems(df):
    """
    برمی‌گرداند لیستی از تاپل‌های:
        (excel_row, original_col_name, canon_name, value, reason)

    شماره سطر اکسل: اولین سمپل کاربر در سطر ۲ اکسل است
    (سطر ۱ سرستون‌ها). پس: excel_row = df_index + 2.
    """
    problems = []
    canon_col = {}
    for c in df.columns:
        canon = canonical_of(c)
        if canon and canon in FEATURE_RANGES and canon not in canon_col:
            canon_col[canon] = c

    n = len(df)
    for i in range(n):
        for canon, col in canon_col.items():
            v = df.iloc[i][col]
            ok, reason = _check_value_or_reason(canon, v)
            if not ok:
                problems.append((i + 2, col, canon, v, reason))
    return problems


def _format_dataset_problems(problems, max_rows=15):
    if not problems:
        return ""
    by_row = {}
    for excel_row, col, canon, v, reason in problems:
        by_row.setdefault(excel_row, []).append(col)
    lines = []
    rows_sorted = sorted(by_row.keys())
    for row in rows_sorted[:max_rows]:
        feats = by_row[row]
        feat_str = ", ".join(f"'{c}'" for c in feats)
        lines.append(f"  Row {row}: feature(s) {feat_str}")
    if len(rows_sorted) > max_rows:
        lines.append(f"  ... and {len(rows_sorted) - max_rows} more row(s)")
    return "\n".join(lines)


def compute_derived_features(base):
    """محاسبه D/t، L/D، Npl و ξ از پایه‌ها (برای Manual Test)."""
    out = {}
    D  = base.get("D")
    L  = base.get("L")
    t  = base.get("t")
    fc = base.get("fc")
    fy = base.get("fy")

    def _ok(*vs):
        return all(v is not None and not (isinstance(v, float) and np.isnan(v))
                   for v in vs)

    if _ok(D, t) and t != 0:
        out["D/t"] = D / t

    if _ok(L, D) and D != 0:
        out["L/D"] = L / D

    if _ok(D, t, fy):
        Di = D - 2.0 * t
        if Di > 0:
            As = np.pi / 4.0 * (D**2 - Di**2)
            Ac = np.pi / 4.0 * (Di**2)
            npl = As * fy
            if _ok(fc):
                npl += Ac * fc
            out["Npl"] = npl / 1000.0
            if _ok(fc) and (Ac * fc) > 0:
                out["ξ"] = (As * fy) / (Ac * fc)
    return out


def get_model_feature_names(model):
    if hasattr(model, "feature_names_in_"):
        try:
            return list(model.feature_names_in_)
        except Exception:
            pass
    if hasattr(model, "named_steps"):
        for step in model.named_steps.values():
            if hasattr(step, "feature_names_in_"):
                try:
                    return list(step.feature_names_in_)
                except Exception:
                    continue
    if hasattr(model, "steps"):
        for _, step in model.steps:
            if hasattr(step, "feature_names_in_"):
                try:
                    return list(step.feature_names_in_)
                except Exception:
                    continue
    return None


def compute_metrics(y_true, y_pred):
    y_true = np.asarray(y_true, dtype=float).ravel()
    y_pred = np.asarray(y_pred, dtype=float).ravel()
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    n = len(y_true)
    if n < 2:
        return None
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    mae = float(np.mean(np.abs(y_true - y_pred)))
    nz = y_true != 0
    mape = (float(np.mean(np.abs((y_true[nz] - y_pred[nz]) / y_true[nz])) * 100.0)
            if nz.sum() > 0 else float("nan"))
    return {"N": n, "R2": r2, "RMSE": rmse, "MAE": mae, "MAPE (%)": mape}


def rank_models(metrics_df):
    if metrics_df is None or metrics_df.empty:
        return metrics_df
    df = metrics_df.copy()
    df["_r2_sort"]   = df["R2"].fillna(-np.inf)
    df["_mape_sort"] = df["MAPE (%)"].fillna(np.inf)
    df = df.sort_values(
        by=["_r2_sort", "_mape_sort"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    df.insert(0, "Rank", range(1, len(df) + 1))
    df = df.drop(columns=["_r2_sort", "_mape_sort"])
    return df


def safe_sheet_name(name):
    invalid = set('[]:*?/\\')
    clean = "".join(c for c in str(name) if c not in invalid).strip()
    return (clean or "Sheet")[:31]


# =========================================================
# AUTO generation of S.T_CS / S.T_SS from S.T
# =========================================================
def _auto_generate_steel_onehot(df):
    """
    اگر دیتافریم ستون 'S.T' داشته باشد ولی ستون‌های
    'S.T_CS' و 'S.T_SS' نداشته باشد، این دو ستون
    به‌طور خودکار از مقادیر خام 'S.T' ساخته می‌شوند.
    """
    if "S.T" not in df.columns:
        return df
    if ("S.T_CS" in df.columns) and ("S.T_SS" in df.columns):
        return df

    s = df["S.T"].astype(str).str.strip().str.lower()

    df = df.copy()
    df["S.T_CS"] = s.apply(
        lambda x: 1 if ("cs" in x or "carbon" in x) else 0
    )
    df["S.T_SS"] = s.apply(
        lambda x: 1 if ("ss" in x or "stainless" in x) else 0
    )
    return df


# =========================================================
# AUTO computation of derived features (D/t, L/D, Npl, ξ)
# =========================================================
def _set_or_replace(df, canon_name, values):
    """اگر ستون با این نام متعارف وجود دارد، مقدارش را به‌روزرسانی کن."""
    for c in df.columns:
        if canonical_of(c) == canon_name:
            df[c] = values
            return
    df[canon_name] = values


def _add_derived_features(df):
    """
    محاسبه خودکار D/t، L/D، Npl و ξ از ویژگی‌های پایه:
        D/t = D / t
        L/D = L / D
        Npl = As*fy + Ac*fc   (kN)
        ξ   = As*fy / Ac*fc
    که:
        Di = D - 2t
        As = π/4 * (D² - Di²)
        Ac = π/4 * Di²
    """
    canon_col = {}
    for c in df.columns:
        canon = canonical_of(c)
        if canon and canon not in canon_col:
            canon_col[canon] = c

    D_src  = canon_col.get("D")
    L_src  = canon_col.get("L")
    t_src  = canon_col.get("t")
    fc_src = canon_col.get("fc")
    fy_src = canon_col.get("fy")

    if D_src is None or t_src is None:
        return df

    D  = pd.to_numeric(df[D_src], errors="coerce")
    t  = pd.to_numeric(df[t_src], errors="coerce")
    L  = pd.to_numeric(df[L_src],  errors="coerce") if L_src  else None
    fc = pd.to_numeric(df[fc_src], errors="coerce") if fc_src else None
    fy = pd.to_numeric(df[fy_src], errors="coerce") if fy_src else None

    df = df.copy()

    # D/t
    with np.errstate(divide="ignore", invalid="ignore"):
        Dt = (D / t)
    Dt = Dt.replace([np.inf, -np.inf], np.nan)
    _set_or_replace(df, "D/t", Dt)

    # L/D
    if L is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            LD = (L / D)
        LD = LD.replace([np.inf, -np.inf], np.nan)
        _set_or_replace(df, "L/D", LD)

    # Npl and ξ
    if fy is not None:
        Di = D - 2.0 * t
        with np.errstate(divide="ignore", invalid="ignore"):
            As = np.pi / 4.0 * (D ** 2 - Di ** 2)
            Ac = np.pi / 4.0 * (Di ** 2)
            npl = As * fy
            if fc is not None:
                npl = npl + Ac * fc
            npl_kn = npl / 1000.0
        _set_or_replace(df, "Npl", npl_kn)

        if fc is not None:
            with np.errstate(divide="ignore", invalid="ignore"):
                xi = (As * fy) / (Ac * fc)
            xi = xi.replace([np.inf, -np.inf], np.nan)
            _set_or_replace(df, "ξ", xi)

    return df


# =========================================================
# Main Application
# =========================================================
class PredictorApp(tk.Tk):
    def __init__(self):
        super().__init__()

        # --- High-DPI scaling ---
        self._monitor_dpi = DPI_OVERRIDE if DPI_OVERRIDE else _get_monitor_dpi()
        try:
            # tk scaling بر حسب پیکسل به ازای هر پوینت (72pt = 1 اینچ)
            self.tk.call("tk", "scaling", self._monitor_dpi / 72.0)
        except Exception:
            pass

        # --- F11 fullscreen ، Escape خروج از fullscreen ---
        self._is_fullscreen = False
        self.bind("<F11>",    self._toggle_fullscreen)
        self.bind("<Escape>", lambda e: self._exit_fullscreen())

        self.title("Multi-Model Nu Predictor")
        self.geometry("1200x860")
        self.minsize(1080, 720)
        self.configure(bg=BG)

        self.model_cache = {}
        self.selected_file = None
        self.df_new = None
        self.y_measured = None
        self.per_model_preds = {}
        self.predictions_df = None
        self.output_df = None
        self.metrics_df = None
        self.success_models = []
        self.failed_models = []

        self.uploaded_pkl = {}
        self.pkl_folder   = None

        self.mt_all_feats     = []
        self.mt_input_feats   = []
        self.mt_derived_feats = []
        self.mt_skin_feats    = []
        self.mt_rows          = []
        self.mt_results_df    = None

        # === Manual Test window state (separate window) ===
        self.mt_window        = None
        self.mt_model_var     = tk.StringVar()
        self.mt_model_cb      = None
        self.mt_canvas        = None
        self.mt_inner         = None
        self.mt_results_tree  = None
        self.mt_run_btn       = None
        self.mt_export_btn    = None
        self.mt_export_excel_btn = None
        self.mt_range_frame   = None
        self.mt_warn_var      = tk.StringVar(value="")
        self.mt_run_status_var = tk.StringVar(
            value="Independent from dataset run — runs all rows using the selected model.")

        self._setup_style()
        self._build_ui()
        self.after(200, self._log_dpi_info)
        self.after(600, self._init_manual_tab)

    # -----------------------------------------------------
    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=BG, foreground=PRIMARY,
                        font=("Segoe UI Semibold", 15))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED,
                        font=("Segoe UI", 9))
        style.configure("TLabelframe", background=BG, bordercolor=BORDER)
        style.configure("TLabelframe.Label", background=BG, foreground=PRIMARY,
                        font=("Segoe UI Semibold", 11))
        style.configure("TEntry", fieldbackground=PANEL, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=6)
        # === استایل خاص برای ورودی خارج از بازه (Manual Test) ===
        style.configure("Error.TEntry",
                        fieldbackground="#FEE2E2",
                        foreground="#7F1D1D",
                        bordercolor=DANGER,
                        lightcolor=DANGER,
                        darkcolor=DANGER,
                        padding=6)
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=(12, 7))
        style.configure("Primary.TButton", background=PRIMARY, foreground="white",
                        borderwidth=0)
        style.map("Primary.TButton",
                  background=[("active", PRIMARY_DK), ("disabled", "#93C5FD")],
                  foreground=[("disabled", "white")])
        style.configure("Success.TButton", background=SUCCESS, foreground="white",
                        borderwidth=0)
        style.map("Success.TButton",
                  background=[("active", SUCCESS_DK), ("disabled", "#86EFAC")],
                  foreground=[("disabled", "white")])
        style.configure("Ghost.TButton", background=PANEL, foreground=TEXT,
                        borderwidth=1, relief="solid")
        style.map("Ghost.TButton", background=[("active", ACCENT_BG)])
        style.configure("Upload.TButton", background="#0891B2", foreground="white",
                        borderwidth=0, font=("Segoe UI Semibold", 10),
                        padding=(14, 8))
        style.map("Upload.TButton",
                  background=[("active", "#0E7490"), ("disabled", "#A5F3FC")],
                  foreground=[("disabled", "white")])
        style.configure("Run.TButton", background="#7C3AED", foreground="white",
                        borderwidth=0, font=("Segoe UI Semibold", 11),
                        padding=(18, 8))
        style.map("Run.TButton",
                  background=[("active", "#6D28D9"), ("disabled", "#C4B5FD")],
                  foreground=[("disabled", "white")])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT,
                        font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI Semibold", 10),
                        padding=(16, 8), background="#E5E7EB", foreground=TEXT)
        style.map("TNotebook.Tab",
                  background=[("selected", PANEL)],
                  foreground=[("selected", PRIMARY)])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, rowheight=26, font=("Segoe UI", 9),
                        bordercolor=BORDER)
        style.configure("Treeview.Heading", background="#E5E7EB", foreground=TEXT,
                        font=("Segoe UI Semibold", 9))
        style.map("Treeview", background=[("selected", "#BFDBFE")],
                  foreground=[("selected", TEXT)])
        style.configure("TProgressbar", troughcolor="#E5E7EB",
                        background=PRIMARY, bordercolor=BORDER)
        style.configure("Status.TLabel", background=BG, foreground=MUTED,
                        font=("Segoe UI", 9, "italic"))

    # -----------------------------------------------------
    def _build_ui(self):
        header = ttk.Frame(self)
        header.pack(fill="x", padx=16, pady=(14, 6))
        ttk.Label(header, text="Multi-Model Nu Predictor",
                  style="Title.TLabel").pack(anchor="w")
        ttk.Label(header,
                  text="Select PKL folder, load dataset, run selected models, view predictions.",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))

        pkl_panel = ttk.LabelFrame(self, text=" 0. Select PKL Folder ", padding=12)
        pkl_panel.pack(fill="x", padx=16, pady=(6, 6))
        ttk.Button(pkl_panel, text="📂  Browse PKL Folder...", style="Upload.TButton",
                   command=self.select_pkl_folder).pack(side="left")
        self.pkl_info_var = tk.StringVar(
            value="No folder selected yet. Click 'Browse PKL Folder' to load models.")
        ttk.Label(pkl_panel, textvariable=self.pkl_info_var,
                  style="Panel.TLabel", foreground=MUTED,
                  font=("Segoe UI", 9, "italic"),
                  wraplength=900, justify="left").pack(side="left", padx=(12, 0))

        file_panel = ttk.LabelFrame(self, text=" 1. Dataset ", padding=12)
        file_panel.pack(fill="x", padx=16, pady=(0, 8))
        self.file_var = tk.StringVar(value="No file selected")
        ttk.Entry(file_panel, textvariable=self.file_var,
                  font=("Segoe UI", 10)).pack(side="left", fill="x",
                                              expand=True, padx=(0, 10))
        ttk.Button(file_panel, text="Browse...", style="Ghost.TButton",
                   command=self.browse_file).pack(side="left")
        self.file_info_var = tk.StringVar(value="")
        ttk.Label(file_panel, textvariable=self.file_info_var,
                  style="Panel.TLabel", foreground=MUTED,
                  font=("Segoe UI", 9, "italic")).pack(side="left", padx=(12, 0))

        models_panel = ttk.LabelFrame(self, text=" 2. Models ", padding=12)
        models_panel.pack(fill="x", padx=16, pady=(0, 8))
        self.models_grid = ttk.Frame(models_panel, style="Panel.TFrame")
        self.models_grid.pack(fill="x")
        self.model_vars = {}
        self.model_status_vars = {}
        self._build_models_panel()

        btn_row = ttk.Frame(models_panel, style="Panel.TFrame")
        btn_row.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_row, text="Select All", style="Ghost.TButton",
                   command=lambda: self.toggle_all(True)).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Deselect All", style="Ghost.TButton",
                   command=lambda: self.toggle_all(False)).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Check Model Files", style="Ghost.TButton",
                   command=lambda: self.check_models_dialog(show_dialog=True)
                   ).pack(side="left")

        action = ttk.Frame(self)
        action.pack(fill="x", padx=16, pady=(0, 6))
        self.predict_btn = ttk.Button(action, text="Run Prediction",
                                      style="Primary.TButton",
                                      command=self.run_prediction)
        self.predict_btn.pack(side="left", padx=(0, 8))

        # === Open Manual Test in a separate window ===
        self.open_mt_btn = ttk.Button(
            action, text="🧪  Open Manual Test",
            style="Run.TButton",
            command=self._open_manual_test_window)
        self.open_mt_btn.pack(side="left", padx=(0, 8))

        self.export_excel_btn = ttk.Button(
            action, text="Export Excel (Multi-Sheet)", style="Success.TButton",
            command=lambda: self.export_results("xlsx"), state="disabled")
        self.export_excel_btn.pack(side="left", padx=4)
        self.export_csv_btn = ttk.Button(
            action, text="Export CSV", style="Success.TButton",
            command=lambda: self.export_results("csv"), state="disabled")
        self.export_csv_btn.pack(side="left", padx=4)

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=16, pady=(4, 2))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, style="Status.TLabel")\
            .pack(fill="x", padx=16)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=16, pady=(8, 14))
        self.nb = nb

        tab1 = ttk.Frame(nb, style="Panel.TFrame")
        nb.add(tab1, text="  Predictions  ")
        self.preds_tree = self._make_treeview(tab1)

        tab_log = ttk.Frame(nb, style="Panel.TFrame")
        nb.add(tab_log, text="  Log  ")
        self.log_text = ScrolledText(tab_log, wrap="word", height=12,
                                     bg="#0B1220", fg="#E5E7EB",
                                     insertbackground="#E5E7EB",
                                     font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, padx=10, pady=10)

    # -----------------------------------------------------
    def _all_model_names(self):
        return list(self.uploaded_pkl.keys())

    def _build_models_panel(self):
        old_vars   = dict(self.model_vars)
        old_status = dict(self.model_status_vars)

        for w in self.models_grid.winfo_children():
            w.destroy()

        self.model_vars = {}
        self.model_status_vars = {}

        names = self._all_model_names()
        if not names:
            ttk.Label(self.models_grid,
                      text="No models loaded yet. Please select a PKL folder above.",
                      style="Panel.TLabel", foreground=MUTED,
                      font=("Segoe UI", 9, "italic")).grid(
                row=0, column=0, padx=8, pady=6, sticky="w")
            return

        ncol = 3
        for i, name in enumerate(names):
            var = old_vars.get(name, tk.BooleanVar(value=True))
            self.model_vars[name] = var
            r, c = divmod(i, ncol)
            cell = ttk.Frame(self.models_grid, style="Panel.TFrame")
            cell.grid(row=r, column=c, sticky="w", padx=8, pady=3)
            ttk.Checkbutton(cell, text=name, variable=var).pack(side="left")
            st = old_status.get(name, tk.StringVar(value="…"))
            self.model_status_vars[name] = st
            ttk.Label(cell, textvariable=st, style="Panel.TLabel",
                      foreground=MUTED, font=("Segoe UI", 8, "italic")
                      ).pack(side="left", padx=(6, 0))

    # -----------------------------------------------------
    def _make_treeview(self, parent):
        wrap = ttk.Frame(parent, style="Panel.TFrame")
        wrap.pack(fill="both", expand=True, padx=10, pady=10)
        tree = ttk.Treeview(wrap, show="headings")
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        return tree

    def _populate_tree(self, tree, df):
        tree.delete(*tree.get_children())
        tree["columns"] = list(df.columns)
        for c in df.columns:
            tree.heading(c, text=str(c))
            tree.column(c, width=140, anchor="center", stretch=True)
        for _, row in df.iterrows():
            tree.insert("", "end", values=[self._fmt(v) for v in row])

    @staticmethod
    def _fmt(v):
        if isinstance(v, float):
            if np.isnan(v):
                return "—"
            return f"{v:.4f}"
        return v

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        def _append():
            try:
                self.log_text.insert("end", line)
                self.log_text.see("end")
            except Exception:
                pass
        self.after(0, _append)

    def _clear_log(self):
        try:
            self.log_text.delete("1.0", "end")
        except Exception:
            pass

    # -----------------------------------------------------
    # PKL FOLDER selection
    # -----------------------------------------------------
    def select_pkl_folder(self):
        folder = filedialog.askdirectory(
            title="Select folder containing PKL models")
        if not folder:
            return
        folder_path = Path(folder).resolve()

        pkl_files = []
        for ext in ("*.pkl", "*.joblib", "*.sav"):
            pkl_files.extend(sorted(folder_path.glob(ext)))

        if not pkl_files:
            messagebox.showwarning(
                "No PKL files",
                f"No .pkl / .joblib / .sav files found in:\n{folder_path}")
            return

        self.uploaded_pkl.clear()
        self.model_cache.clear()

        added, errors = [], []
        for p in pkl_files:
            try:
                _ = joblib.load(str(p))
                base = p.stem
                name = base
                i = 1
                while name in self.uploaded_pkl:
                    name = f"{base}_{i}"
                    i += 1
                self.uploaded_pkl[name] = str(p)
                added.append(name)
            except Exception as e:
                errors.append(f"{p.name}: {type(e).__name__}: {e}")

        self.pkl_folder = folder_path

        if added:
            self._build_models_panel()
            # Only update the Manual Test window if it currently exists
            if self.mt_model_cb is not None and self.mt_window is not None \
                    and self.mt_window.winfo_exists():
                try:
                    self.mt_model_cb["values"] = self._all_model_names()
                    if not self.mt_model_var.get() or \
                            self.mt_model_var.get() not in self.uploaded_pkl:
                        self.mt_model_var.set(added[0])
                        self._build_manual_inputs()
                except Exception:
                    pass

        self.pkl_info_var.set(
            f"Folder: {folder_path}  |  "
            f"✓ {len(self.uploaded_pkl)} model(s) loaded"
            + (f"  |  ✗ {len(errors)} failed" if errors else ""))

        self.log(f"==== PKL folder selected ====")
        self.log(f"  Folder: {folder_path}")
        self.log(f"  Found {len(pkl_files)} candidate file(s)")
        for n in added:
            self.log(f"  ✓ {n}  ->  {self.uploaded_pkl[n]}")
        for e in errors:
            self.log(f"  ✗ {e}")

        if added and not errors:
            messagebox.showinfo(
                "PKL Folder Loaded",
                f"Loaded {len(added)} model(s) from:\n{folder_path}\n\n"
                + "\n".join(added))
        elif added and errors:
            messagebox.showwarning(
                "Partial Load",
                f"Loaded {len(added)} model(s) from:\n{folder_path}\n\n"
                f"Success:\n" + "\n".join(added)
                + "\n\nFailed:\n" + "\n".join(errors))
        else:
            messagebox.showerror(
                "Load failed",
                f"No model could be loaded from:\n{folder_path}\n\n"
                + "\n".join(errors))

    # -----------------------------------------------------
    def _model_path(self, name):
        if name in self.uploaded_pkl:
            return Path(self.uploaded_pkl[name])
        return None

    def _iter_all_models(self):
        for name, path in self.uploaded_pkl.items():
            yield name, Path(path)

    def check_models_dialog(self, show_dialog=True):
        if not self.uploaded_pkl:
            if show_dialog:
                messagebox.showinfo(
                    "No models",
                    "No models loaded yet.\nPlease select a PKL folder first.")
            return

        report = []
        ok, missing, broken = [], [], []
        for name, p in self._iter_all_models():
            if not p.exists():
                missing.append(name)
                self.model_status_vars[name].set("✗ missing")
                report.append(f"MISSING  {name:14s} -> {p}")
                continue
            try:
                m = self._load_model(name, p)
                feats = get_model_feature_names(m)
                n_feat = len(feats) if feats else "?"
                self.model_status_vars[name].set(f"✓ ({n_feat} feats)")
                ok.append(name)
                report.append(f"OK       {name:14s} -> {p.name}  (features={n_feat})")
            except Exception as e:
                broken.append((name, str(e)))
                self.model_status_vars[name].set("✗ load err")
                report.append(f"LOAD ERR {name:14s} -> {p.name}\n         {type(e).__name__}: {e}")

        self.log("==== Model check ====")
        for r in report:
            self.log(r)
        self.log(f"OK={len(ok)}  Missing={len(missing)}  Broken={len(broken)}")
        self.log(f"Folder = {self.pkl_folder}")

        if show_dialog:
            msg = (f"Folder: {self.pkl_folder}\n\n"
                   f"OK      : {len(ok)}\n"
                   f"Missing : {len(missing)}  {missing if missing else ''}\n"
                   f"Broken  : {len(broken)}   {[b[0] for b in broken] if broken else ''}\n\n"
                   f"See the Log tab for details.")
            messagebox.showinfo("Model files status", msg)

    def browse_file(self):
        path = filedialog.askopenfilename(
            title="Select dataset",
            filetypes=[("Excel/CSV", "*.xlsx *.xls *.csv"),
                       ("Excel", "*.xlsx *.xls"),
                       ("CSV", "*.csv"),
                       ("All files", "*.*")])
        if not path:
            return
        self.selected_file = path
        self.file_var.set(path)
        try:
            p = Path(path)
            if p.suffix.lower() in (".xlsx", ".xls"):
                self.df_new = pd.read_excel(p, sheet_name=0)
            else:
                try:
                    self.df_new = pd.read_csv(p, encoding="utf-8-sig")
                except UnicodeDecodeError:
                    self.df_new = pd.read_csv(p, encoding="latin-1")
        except Exception as e:
            messagebox.showerror("Read error", f"Failed to read file:\n{e}")
            return

        self.df_new.columns = [str(c).strip() for c in self.df_new.columns]

        # === AUTO: generate S.T_CS / S.T_SS from S.T if needed ===
        before_cols = list(self.df_new.columns)
        self.df_new = _auto_generate_steel_onehot(self.df_new)
        after_cols = list(self.df_new.columns)
        if len(after_cols) > len(before_cols):
            self.log(
                "[AUTO] Generated 'S.T_CS' and 'S.T_SS' from the 'S.T' column."
            )

        # === AUTO: compute derived features D/t, L/D, Npl, ξ ===
        before_cols2 = list(self.df_new.columns)
        self.df_new = _add_derived_features(self.df_new)
        after_cols2 = list(self.df_new.columns)
        added_derived = [c for c in after_cols2 if c not in before_cols2]
        if added_derived:
            self.log(f"[AUTO] Computed derived features: {added_derived}")

        if TARGET_COL in self.df_new.columns:
            self.y_measured = pd.to_numeric(
                self.df_new[TARGET_COL], errors="coerce"
            ).reset_index(drop=True)
        else:
            self.y_measured = None

        self.file_info_var.set(
            f"{p.name} | {self.df_new.shape[0]} rows × {self.df_new.shape[1]} cols"
        )
        self.status_var.set(f"Loaded: {p.name}")
        self.log(f"==== Dataset loaded ====")
        self.log(f"File: {p}")
        self.log(f"Shape: {self.df_new.shape}")
        self.log(f"Columns: {list(self.df_new.columns)}")
        self.log(f"Target '{TARGET_COL}' present: {TARGET_COL in self.df_new.columns}")

        # === RANGE validation on dataset ===
        try:
            problems = _find_dataset_range_problems(self.df_new)
        except Exception:
            problems = []
            self.log("[RANGE] dataset validation error:")
            self.log(traceback.format_exc())

        if problems:
            n_rows_affected = len(set(p_[0] for p_ in problems))
            self.log(f"[RANGE] {len(problems)} out-of-range value(s) in "
                     f"{n_rows_affected} row(s):")
            for line in _format_dataset_problems(problems).split("\n"):
                self.log("  " + line)
            msg = (
                f"The dataset contains {len(problems)} out-of-range value(s) "
                f"in {n_rows_affected} row(s).\n\n"
                f"{_format_dataset_problems(problems)}\n\n"
                f"(Excel row numbers: header is row 1, first data sample is row 2.)"
            )
            messagebox.showwarning("Out-of-range values detected", msg)

    def _load_model(self, name, path):
        if name in self.model_cache:
            return self.model_cache[name]
        model = joblib.load(str(path))
        self.model_cache[name] = model
        return model

    def toggle_all(self, value):
        for v in self.model_vars.values():
            v.set(value)

    def _X_for_model(self, name, model):
        feats = get_model_feature_names(model)
        if feats is None:
            self.log(f"  [{name}] no feature_names_in_ → using all non-target cols")
            return self.df_new.drop(columns=[TARGET_COL], errors="ignore").copy()

        user_by_canon = {}
        for c in self.df_new.columns:
            canon = canonical_of(c)
            if canon and canon not in user_by_canon:
                user_by_canon[canon] = c

        out = pd.DataFrame(index=self.df_new.index)
        missing = []
        for feat in feats:
            if feat in self.df_new.columns:
                out[feat] = pd.to_numeric(self.df_new[feat],
                                          errors="coerce").values
                continue
            canon = canonical_of(feat)
            if canon and canon in user_by_canon:
                src = user_by_canon[canon]
                out[feat] = pd.to_numeric(self.df_new[src],
                                          errors="coerce").values
                continue
            out[feat] = None
            missing.append(feat)

        if missing:
            raise ValueError(
                f"missing: {missing}. "
                f"Model expects: {feats}. "
                f"YOUR columns: {list(self.df_new.columns)}. "
                f"Canonical map built: {user_by_canon}"
            )
        return out

    # -----------------------------------------------------
    # Prediction (dataset run)
    # -----------------------------------------------------
    def run_prediction(self):
        if not self.uploaded_pkl:
            messagebox.showwarning(
                "No models",
                "Please select a PKL folder first (section 0 at the top).")
            return
        if self.df_new is None:
            messagebox.showwarning("No data", "Please select a valid dataset first.")
            return
        selected = [n for n, v in self.model_vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("No model", "Please select at least one model.")
            return

        self.predict_btn.config(state="disabled")
        self.export_excel_btn.config(state="disabled")
        self.export_csv_btn.config(state="disabled")
        self.progress["value"] = 0
        self._clear_log()

        self.log(f"==== Run Prediction (dataset) ====")
        self.log(f"Models selected ({len(selected)}): {selected}")
        self.log(f"Dataset columns: {list(self.df_new.columns)}")
        self.log(f"Dataset shape: {self.df_new.shape}")
        self.log(f"Nu_Kn present: {TARGET_COL in self.df_new.columns}")

        threading.Thread(target=self._predict_worker,
                         args=(selected,), daemon=True).start()

    def _predict_worker(self, selected):
        per_model = {}
        succeeded, failed = [], []
        n = len(selected)

        for i, name in enumerate(selected):
            path = self._model_path(name)
            self._set_status(f"[{i+1}/{n}] {name}: loading…")
            self.log(f"---- {name} ----")

            if path is None or not path.exists():
                msg = f"file not found: {path}"
                failed.append((name, msg)); self.log(f"  ✗ {msg}")
                self._set_progress(int((i + 1) / n * 100)); continue

            try:
                model = self._load_model(name, path)
                self.log(f"  ✓ loaded ({type(model).__name__})")
            except Exception as e:
                msg = f"load failed: {type(e).__name__}: {e}"
                failed.append((name, msg)); self.log(f"  ✗ {msg}")
                self.log(traceback.format_exc())
                self._set_progress(int((i + 1) / n * 100)); continue

            try:
                X_use = self._X_for_model(name, model)
                self.log(f"  X shape = {X_use.shape}  cols = {list(X_use.columns)}")
            except Exception as e:
                msg = f"input prep failed: {e}"
                failed.append((name, msg)); self.log(f"  ✗ {msg}")
                self._set_progress(int((i + 1) / n * 100)); continue

            try:
                y_pred = np.asarray(model.predict(X_use)).ravel()
                per_model[name] = y_pred
                succeeded.append(name)
                self.log(f"  ✓ predicted  n={len(y_pred)}  "
                         f"mean={np.nanmean(y_pred):.3f}  "
                         f"std={np.nanstd(y_pred):.3f}")
            except Exception as e:
                msg = f"predict failed: {type(e).__name__}: {e}"
                failed.append((name, msg)); self.log(f"  ✗ {msg}")
                self.log(traceback.format_exc())

            self._set_progress(int((i + 1) / n * 100))
            self._set_status(f"[{i+1}/{n}] done: {name}")

        self.per_model_preds = per_model
        self.success_models = succeeded
        self.failed_models = failed

        if succeeded:
            results = pd.DataFrame({f"{m}_Pred": per_model[m] for m in succeeded})
        else:
            results = pd.DataFrame()
        self.predictions_df = results

        metrics_rows = []
        if self.y_measured is not None and not results.empty:
            y_true = self.y_measured.values.astype(float)
            for m in succeeded:
                col = f"{m}_Pred"
                mt = compute_metrics(y_true, results[col].values)
                if mt is not None:
                    metrics_rows.append({
                        "Model": m, "N": mt["N"],
                        "R2": mt["R2"], "RMSE": mt["RMSE"],
                        "MAE": mt["MAE"], "MAPE (%)": mt["MAPE (%)"],
                    })
        metrics_df = pd.DataFrame(metrics_rows) if metrics_rows else None

        if metrics_df is not None and len(metrics_df) > 1:
            metrics_df = rank_models(metrics_df)
            self.log("---- Model ranking (R2 ↓, then MAPE ↑) ----")
            for _, r in metrics_df.iterrows():
                self.log(
                    f"  #{int(r['Rank'])}  {r['Model']:14s}  "
                    f"R2={r['R2']:.4f}  MAPE={r['MAPE (%)']:.2f}%  "
                    f"RMSE={r['RMSE']:.3f}  MAE={r['MAE']:.3f}"
                )
            best = metrics_df.iloc[0]
            self.log(
                f"  ★ BEST MODEL: {best['Model']} "
                f"(R2={best['R2']:.4f}, MAPE={best['MAPE (%)']:.2f}%)"
            )
        elif metrics_df is not None and len(metrics_df) == 1:
            metrics_df = rank_models(metrics_df)
        self.metrics_df = metrics_df

        base = self.df_new.drop(columns=[TARGET_COL], errors="ignore").reset_index(drop=True)
        if not results.empty:
            self.output_df = pd.concat([base, results.reset_index(drop=True)], axis=1)
        else:
            self.output_df = base

        self.log(f"==== Summary (dataset) ====")
        self.log(f"SUCCEEDED ({len(succeeded)}): {succeeded}")
        self.log(f"FAILED    ({len(failed)}): {[f[0] for f in failed]}")
        for name, msg in failed:
            self.log(f"   {name}: {msg}")

        self.after(0, lambda: self._finish_prediction(succeeded, failed))

    def _finish_prediction(self, succeeded, failed):
        self._populate_tree(self.preds_tree, self.predictions_df.head(1000))

        self.predict_btn.config(state="normal")
        if len(self.predictions_df.columns) > 0:
            self.export_excel_btn.config(state="normal")
            self.export_csv_btn.config(state="normal")
        self.progress["value"] = 100

        self.status_var.set(
            f"Done. Succeeded: {len(succeeded)}  |  Failed: {len(failed)}"
        )

        if failed:
            msg = "Some models failed:\n\n" + "\n".join(
                f"• {n}: {m}" for n, m in failed
            )
            if not succeeded:
                msg = ("ALL models failed. Check the Log tab.\n\n" + msg)
            messagebox.showwarning("Model failures", msg[:2000])
        elif succeeded:
            messagebox.showinfo(
                "Done",
                f"Successfully predicted with {len(succeeded)} model(s).\n"
                f"Click 'Export Excel (Multi-Sheet)' to save."
            )

    # -----------------------------------------------------
    # Manual Test — separate window
    # -----------------------------------------------------
    def _open_manual_test_window(self):
        # If window already exists (even if hidden) → just show it
        if self.mt_window is not None and self.mt_window.winfo_exists():
            self.mt_window.deiconify()
            self.mt_window.lift()
            self.mt_window.focus_force()
            return

        # ---- Create new Toplevel ----
        self.mt_window = tk.Toplevel(self)
        self.mt_window.title("Manual Test — Multi-Model Nu Predictor")
        self.mt_window.geometry("1400x820")
        self.mt_window.minsize(1000, 600)
        self.mt_window.configure(bg=BG)

        # --- High-DPI scaling for the new window ---
        try:
            self.mt_window.tk.call("tk", "scaling", self._monitor_dpi / 72.0)
        except Exception:
            pass

        # --- F11 fullscreen + Escape for the manual window ---
        self.mt_window._is_fullscreen = False

        def _mt_toggle_fullscreen(event=None):
            self.mt_window._is_fullscreen = not self.mt_window._is_fullscreen
            self.mt_window.attributes("-fullscreen", self.mt_window._is_fullscreen)
            try:
                dpi = DPI_OVERRIDE if DPI_OVERRIDE else _get_monitor_dpi()
                self._monitor_dpi = dpi
                self.mt_window.tk.call("tk", "scaling", dpi / 72.0)
            except Exception:
                pass
            return "break"

        def _mt_exit_fullscreen(event=None):
            self.mt_window._is_fullscreen = False
            self.mt_window.attributes("-fullscreen", False)

        self.mt_window.bind("<F11>", _mt_toggle_fullscreen)
        self.mt_window.bind("<Escape>", _mt_exit_fullscreen)

        # Closing the window hides it (does NOT destroy → data preserved)
        self.mt_window.protocol("WM_DELETE_WINDOW", self._hide_manual_test_window)

        # --- Header inside the new window ---
        header = ttk.Frame(self.mt_window)
        header.pack(fill="x", padx=16, pady=(14, 6))
        ttk.Label(header, text="Manual Test",
                  style="Title.TLabel").pack(anchor="w")
        ttk.Label(header,
                  text="Enter values manually and predict with the selected model.  "
                       "(F11 = fullscreen, Esc = exit)",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))

        # --- Back button ---
        back_row = ttk.Frame(self.mt_window)
        back_row.pack(fill="x", padx=16, pady=(0, 6))
        ttk.Button(back_row, text="← Back to Main",
                   style="Ghost.TButton",
                   command=self._hide_manual_test_window).pack(side="left")

        tab_mt = ttk.Frame(self.mt_window, style="Panel.TFrame")
        tab_mt.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        mt_ctrl = ttk.Frame(tab_mt, style="Panel.TFrame")
        mt_ctrl.pack(fill="x", padx=12, pady=(10, 4))
        ttk.Label(mt_ctrl, text="Model:", style="Panel.TLabel").pack(side="left")
        self.mt_model_cb = ttk.Combobox(
            mt_ctrl, textvariable=self.mt_model_var,
            state="readonly", width=22, values=self._all_model_names())
        self.mt_model_cb.pack(side="left", padx=(6, 10))
        self.mt_model_cb.bind("<<ComboboxSelected>>",
                              self._on_mt_model_change)
        ttk.Button(mt_ctrl, text="+ Add Row", style="Ghost.TButton",
                   command=self._add_manual_row).pack(side="left", padx=4)
        ttk.Button(mt_ctrl, text="Clear All", style="Ghost.TButton",
                   command=self._clear_manual_rows).pack(side="left", padx=4)
        ttk.Button(mt_ctrl, text="▶  Run", style="Primary.TButton",
                   command=self._run_manual_prediction).pack(side="left", padx=(10, 4))

        # === Range box (acceptable ranges for input features) ===
        self.mt_range_frame = ttk.LabelFrame(
            tab_mt, text=" Acceptable Ranges (for the features you enter) ",
            padding=8)
        self.mt_range_frame.pack(fill="x", padx=12, pady=(0, 4))

        # === Warning line ===
        ttk.Label(tab_mt, textvariable=self.mt_warn_var,
                  style="Panel.TLabel", foreground=DANGER,
                  font=("Segoe UI", 9, "bold")).pack(
            fill="x", padx=12, pady=(0, 2))

        mt_input_wrap = ttk.Frame(tab_mt, style="Panel.TFrame")
        mt_input_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        self.mt_canvas = tk.Canvas(mt_input_wrap, bg=PANEL,
                                   highlightthickness=0)
        mt_vsb = ttk.Scrollbar(mt_input_wrap, orient="vertical",
                               command=self.mt_canvas.yview)
        mt_hsb = ttk.Scrollbar(mt_input_wrap, orient="horizontal",
                               command=self.mt_canvas.xview)
        self.mt_canvas.configure(yscrollcommand=mt_vsb.set,
                                 xscrollcommand=mt_hsb.set)

        self.mt_canvas.grid(row=0, column=0, sticky="nsew")
        mt_vsb.grid(row=0, column=1, sticky="ns")
        mt_hsb.grid(row=1, column=0, sticky="ew")
        mt_input_wrap.rowconfigure(0, weight=1)
        mt_input_wrap.columnconfigure(0, weight=1)

        self.mt_inner = ttk.Frame(self.mt_canvas, style="Panel.TFrame")
        self._mt_window_id = self.mt_canvas.create_window(
            (0, 0), window=self.mt_inner, anchor="nw")

        def _on_mt_inner(_e):
            self.mt_canvas.configure(scrollregion=self.mt_canvas.bbox("all"))
        self.mt_inner.bind("<Configure>", _on_mt_inner)

        # === فعال‌سازی اسکرول موس در بخش Manual Test ===
        self.bind_all("<MouseWheel>", self._on_mousewheel)

        mt_run = ttk.Frame(tab_mt, style="Panel.TFrame")
        mt_run.pack(fill="x", padx=12, pady=(4, 6))
        self.mt_run_btn = ttk.Button(
            mt_run, text="▶  RUN  (All Rows)", style="Run.TButton",
            command=self._run_manual_prediction)
        self.mt_run_btn.pack(side="left")

        # === Export CSV (same conventions as main page) ===
        self.mt_export_btn = ttk.Button(
            mt_run, text="Export Manual Results (CSV)", style="Success.TButton",
            command=self._export_manual_results, state="disabled")
        self.mt_export_btn.pack(side="left", padx=8)

        # === Export Excel (multi-sheet) — identical conventions to main page ===
        self.mt_export_excel_btn = ttk.Button(
            mt_run, text="Export Manual Results (Excel)", style="Success.TButton",
            command=self._export_manual_results_excel, state="disabled")
        self.mt_export_excel_btn.pack(side="left", padx=4)

        ttk.Label(mt_run, textvariable=self.mt_run_status_var,
                  style="Panel.TLabel", foreground=MUTED,
                  font=("Segoe UI", 9, "italic")).pack(side="left", padx=(12, 0))

        self.mt_results_tree = self._make_treeview(tab_mt)

        # --- Initialize model selection / inputs ---
        names = self._all_model_names()
        self.mt_model_cb["values"] = names
        if names and not self.mt_model_var.get():
            self.mt_model_var.set(names[0])
        self._build_manual_inputs()

    def _hide_manual_test_window(self):
        """Hide the Manual Test window (does NOT destroy → data preserved)."""
        if self.mt_window is not None and self.mt_window.winfo_exists():
            self.mt_window.withdraw()
        # Return focus to main window
        self.deiconify()
        self.lift()
        self.focus_force()

    def _init_manual_tab(self):
        names = self._all_model_names()
        if names and not self.mt_model_var.get():
            self.mt_model_var.set(names[0])
        # Only touch the combo box if the window exists
        if self.mt_model_cb is not None and self.mt_window is not None \
                and self.mt_window.winfo_exists():
            try:
                self.mt_model_cb["values"] = names
            except Exception:
                pass
            if names:
                self._build_manual_inputs()

    def _on_mt_model_change(self, event=None):
        """هنگام تغییر مدل، مقادیر واردشده در سطرها حفظ می‌شوند."""
        # Guard: nothing to preserve if inputs not built yet
        if self.mt_inner is None or not self.mt_inner.winfo_exists():
            return

        # ۱) ذخیره مقادیر فعلی بر اساس نام متعارف
        captured = []
        for row in self.mt_rows:
            vals = {}
            for j, e in enumerate(row["entries"]):
                f = self.mt_input_feats[j]
                c = canonical_of(f)
                if c:
                    vals[c] = e.get()
            skin = (row["skin_var"].get()
                    if row["skin_var"] is not None else "Carbon Steel")
            captured.append({"vals": vals, "skin": skin})

        # ۲) بازسازی رابط کاربری
        self._build_manual_inputs()

        # ۳) بازیابی مقادیر
        for i, cap in enumerate(captured):
            while i >= len(self.mt_rows):
                self._add_manual_row()
            row = self.mt_rows[i]
            for j, e in enumerate(row["entries"]):
                f = self.mt_input_feats[j]
                c = canonical_of(f)
                if c and c in cap["vals"]:
                    e.delete(0, "end")
                    e.insert(0, cap["vals"][c])
                    self._on_mt_entry_change(e, f)
            if row["skin_var"] is not None:
                row["skin_var"].set(cap["skin"])

    def _on_mousewheel(self, event):
        """اسکرول موس فقط زمانی فعال است که نشانگر روی mt_canvas باشد."""
        try:
            if self.mt_canvas is None:
                return
            widget = self.winfo_containing(event.x_root, event.y_root)
            w = widget
            while w is not None:
                if w is self.mt_canvas:
                    self.mt_canvas.yview_scroll(
                        int(-1 * (event.delta / 120)), "units")
                    return
                w = w.master
        except Exception:
            pass

    @staticmethod
    def _header_with_unit(feat_name):
        display_name = str(feat_name)
        if display_name.strip() in ("ꭓ", "χ", "X"):
            display_name = "ξ"
        u = unit_of(feat_name)
        if not u:
            return display_name
        if u == "%" and "%" in display_name:
            return display_name
        if u.lower() in display_name.lower():
            return display_name
        return f"{display_name} ({u})"

    # -----------------------------------------------------
    # Manual Test: real-time range validation on an entry
    # -----------------------------------------------------
    def _on_mt_entry_change(self, entry_widget, feat_name):
        """اعتبارسنجی آنی (همان لحظه) ورودی در Manual Test."""
        try:
            canon = canonical_of(feat_name)
        except Exception:
            canon = None
        if canon is None or canon not in FEATURE_RANGES:
            try:
                entry_widget.configure(style="TEntry")
            except Exception:
                pass
            return
        try:
            txt = entry_widget.get().strip()
        except Exception:
            return
        if txt == "":
            try:
                entry_widget.configure(style="TEntry")
            except Exception:
                pass
            return
        ok, reason = _check_value_or_reason(canon, txt)
        try:
            entry_widget.configure(style="TEntry" if ok else "Error.TEntry")
        except Exception:
            pass
        if not ok:
            lo, hi = FEATURE_RANGES[canon]
            self.mt_warn_var.set(
                f"⚠  '{feat_name}' = {txt} is out of range "
                f"(valid: {lo:g} … {hi:g})  —  {reason}"
            )
        else:
            self.mt_warn_var.set("")

    # -----------------------------------------------------
    def _build_manual_inputs(self):
        # Guard: window / widgets must exist
        if self.mt_inner is None or not self.mt_inner.winfo_exists():
            return

        for w in self.mt_inner.winfo_children():
            w.destroy()

        # === Clear & rebuild the range box ===
        try:
            if self.mt_range_frame is not None and self.mt_range_frame.winfo_exists():
                for w in self.mt_range_frame.winfo_children():
                    w.destroy()
        except Exception:
            pass

        self.mt_rows = []
        self.mt_all_feats     = []
        self.mt_input_feats   = []
        self.mt_derived_feats = []
        self.mt_skin_feats    = []
        try:
            self.mt_warn_var.set("")
        except Exception:
            pass

        name = self.mt_model_var.get()
        if not name:
            if self.mt_range_frame is not None and self.mt_range_frame.winfo_exists():
                ttk.Label(self.mt_range_frame,
                          text="Please select a PKL folder first "
                               "(section 0 at the top).",
                          style="Panel.TLabel", foreground=MUTED,
                          font=("Segoe UI", 9, "italic")).grid(
                    row=0, column=0, sticky="w", padx=4, pady=4)
            ttk.Label(self.mt_inner,
                      text="Please select a PKL folder first (section 0 at the top).",
                      style="Panel.TLabel", foreground=MUTED,
                      font=("Segoe UI", 10, "italic")).grid(
                row=0, column=0, padx=12, pady=20, sticky="w")
            return

        path = self._model_path(name)
        if path is None or not path.exists():
            messagebox.showwarning("Model file missing",
                                   f"Model file not found:\n{path}")
            return

        try:
            model = self._load_model(name, path)
            feats = get_model_feature_names(model)
            if feats is None:
                messagebox.showwarning(
                    "No feature names",
                    f"Model '{name}' has no feature_names_in_ attribute.")
                return
        except Exception as e:
            messagebox.showerror("Load error", f"{type(e).__name__}: {e}")
            return

        self.mt_all_feats = list(feats)

        for f in self.mt_all_feats:
            c = canonical_of(f)
            if c in DERIVED_FEATURES:
                self.mt_derived_feats.append(f)
            elif c in SKIN_TYPE_FEATURES:
                self.mt_skin_feats.append(f)
            else:
                self.mt_input_feats.append(f)

        self.log(f"---- Manual Test: {name} ----")
        self.log(f"  input features   : {self.mt_input_feats}")
        self.log(f"  derived (auto)   : {self.mt_derived_feats}")
        self.log(f"  skin type (drop) : {self.mt_skin_feats}")

        # === Populate the range box (only features user enters) ===
        self._populate_range_box()

        col = 0
        ttk.Label(self.mt_inner, text="#", style="Panel.TLabel",
                  font=("Segoe UI Semibold", 9)).grid(
            row=0, column=col, padx=6, pady=6, sticky="w")
        col += 1
        for f in self.mt_input_feats:
            ttk.Label(self.mt_inner, text=self._header_with_unit(f),
                      style="Panel.TLabel",
                      font=("Segoe UI Semibold", 9),
                      foreground=PRIMARY).grid(
                row=0, column=col, padx=6, pady=6, sticky="w")
            col += 1
        if self.mt_skin_feats:
            ttk.Label(self.mt_inner, text="Skin Type",
                      style="Panel.TLabel",
                      font=("Segoe UI Semibold", 9),
                      foreground=PRIMARY).grid(
                row=0, column=col, padx=6, pady=6, sticky="w")
            col += 1

        ttk.Label(self.mt_inner, text="Predicted Nu",
                  style="Panel.TLabel",
                  font=("Segoe UI Semibold", 9),
                  foreground=SUCCESS).grid(
            row=0, column=col, padx=(14, 6), pady=6, sticky="w")

        self._add_manual_row()

    def _populate_range_box(self):
        """کادر بازه‌های مجاز را برای فیچرهای ورودی Manual Test پر می‌کند."""
        if self.mt_range_frame is None or not self.mt_range_frame.winfo_exists():
            return

        ranges = []
        for f in self.mt_input_feats:
            canon = canonical_of(f)
            if canon in FEATURE_RANGES:
                lo, hi = FEATURE_RANGES[canon]
                ranges.append((f, canon, lo, hi))

        if not ranges:
            ttk.Label(
                self.mt_range_frame,
                text="No range definition for the input features of this model.",
                style="Panel.TLabel", foreground=MUTED,
                font=("Segoe UI", 9, "italic")).grid(
                row=0, column=0, columnspan=4, sticky="w", padx=4, pady=4)
            return

        # small hint row
        ttk.Label(
            self.mt_range_frame,
            text="Only the features shown below are entered manually; "
                 "derived features (D/t, L/D, Npl, ξ) are computed automatically.",
            style="Panel.TLabel", foreground=MUTED,
            font=("Segoe UI", 8, "italic")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=4, pady=(0, 4))

        ncol = 4
        for idx, (f, canon, lo, hi) in enumerate(ranges):
            r, c = divmod(idx, ncol)
            u = unit_of(f)
            u_str = ""
            if u and u not in ("-",):
                if u == "%" and "%" in str(f):
                    u_str = ""
                else:
                    u_str = f" ({u})"
            text = f"{f}{u_str}:  {lo:g}  →  {hi:g}"
            ttk.Label(self.mt_range_frame, text=text,
                      style="Panel.TLabel",
                      foreground=PRIMARY,
                      font=("Segoe UI", 9, "bold")).grid(
                row=r + 1, column=c, sticky="w", padx=8, pady=2)

    def _add_manual_row(self):
        if not self.mt_all_feats:
            messagebox.showwarning("No features loaded",
                                   "Please select a model first.")
            return
        r = len(self.mt_rows) + 1

        ttk.Label(self.mt_inner,
                  text=str(len(self.mt_rows) + 1),
                  style="Panel.TLabel",
                  font=("Segoe UI", 9)).grid(
            row=r, column=0, padx=6, pady=3, sticky="w")

        col = 1
        entries = []
        for _f in self.mt_input_feats:
            e = ttk.Entry(self.mt_inner, width=12, font=("Segoe UI", 9))
            e.grid(row=r, column=col, padx=6, pady=3, sticky="ew")
            # === live validation (on typing and on focus-out) ===
            e.bind("<KeyRelease>",
                   lambda ev, w=e, f=_f: self._on_mt_entry_change(w, f))
            e.bind("<FocusOut>",
                   lambda ev, w=e, f=_f: self._on_mt_entry_change(w, f))
            col += 1
            entries.append(e)

        skin_var = None
        if self.mt_skin_feats:
            skin_var = tk.StringVar(value="Carbon Steel")
            cb = ttk.Combobox(self.mt_inner, textvariable=skin_var,
                              state="readonly", width=16,
                              values=SKIN_TYPE_OPTIONS, font=("Segoe UI", 9))
            cb.grid(row=r, column=col, padx=6, pady=3, sticky="ew")
            col += 1

        pred_var = tk.StringVar(value="—")
        pred_box = tk.Label(
            self.mt_inner, textvariable=pred_var,
            bg="#F0FDF4", fg=SUCCESS_DK,
            font=("Consolas", 10, "bold"),
            relief="solid", borderwidth=1,
            width=10, anchor="center", padx=6, pady=2)
        pred_box.grid(row=r, column=col, padx=(14, 6), pady=3, sticky="w")

        self.mt_rows.append({
            "entries": entries,
            "skin_var": skin_var,
            "pred_var": pred_var,
        })
        self.mt_inner.update_idletasks()
        self.mt_canvas.configure(scrollregion=self.mt_canvas.bbox("all"))

    def _clear_manual_rows(self):
        for w in self.mt_inner.winfo_children():
            w.destroy()
        self.mt_rows = []
        self.mt_all_feats = []
        self.mt_input_feats = []
        self.mt_derived_feats = []
        self.mt_skin_feats = []
        self.mt_results_df = None
        try:
            self.mt_results_tree.delete(*self.mt_results_tree.get_children())
        except Exception:
            pass
        self.mt_export_btn.config(state="disabled")
        if self.mt_export_excel_btn is not None:
            self.mt_export_excel_btn.config(state="disabled")
        self.mt_run_status_var.set(
            "Independent from dataset run — runs all rows using the selected model.")
        try:
            self.mt_warn_var.set("")
        except Exception:
            pass
        self._build_manual_inputs()

    def _run_manual_prediction(self):
        name = self.mt_model_var.get()
        if not name:
            messagebox.showwarning("No model", "Please select a model first.")
            return
        if not self.mt_all_feats:
            messagebox.showwarning("No features",
                                   "Please select a model first.")
            return
        if not self.mt_rows:
            messagebox.showwarning("No rows",
                                   "Please add at least one row (click '+ Add Row').")
            return

        collected = []
        skipped_empty = 0

        for row in self.mt_rows:
            if "pred_var" in row:
                row["pred_var"].set("—")

        for i, row in enumerate(self.mt_rows):
            all_empty = all(e.get().strip() == "" for e in row["entries"])
            if all_empty:
                skipped_empty += 1
                continue
            collected.append((i, row))

        if not collected:
            messagebox.showwarning("Empty",
                                   "All rows are empty. Please enter at least one value.")
            return

        # === Range validation for all entered values (before predict) ===
        invalid_rows = []
        for orig_idx, row in collected:
            for j, e in enumerate(row["entries"]):
                f = self.mt_input_feats[j]
                canon = canonical_of(f)
                if canon not in FEATURE_RANGES:
                    continue
                txt = e.get().strip()
                if txt == "":
                    continue
                try:
                    float(txt)
                except ValueError:
                    continue  # handled by numeric check below
                ok, reason = _check_value_or_reason(canon, txt)
                if not ok:
                    invalid_rows.append((orig_idx + 1, f, txt, reason))

        if invalid_rows:
            lines = [f"  Row {r}: '{f}' = {v}   ({reason})"
                     for r, f, v, reason in invalid_rows[:10]]
            if len(invalid_rows) > 10:
                lines.append(f"  ... and {len(invalid_rows) - 10} more")
            messagebox.showwarning(
                "Out-of-range values",
                "Please fix the following out-of-range values before running:\n\n"
                + "\n".join(lines))
            return

        rows_data = []
        for orig_idx, row in collected:
            base = {}
            for j, e in enumerate(row["entries"]):
                f = self.mt_input_feats[j]
                txt = e.get().strip()
                if txt == "":
                    base[f] = np.nan
                else:
                    try:
                        base[f] = float(txt)
                    except ValueError:
                        u = unit_of(f)
                        u_txt = f" (unit: {u})" if u else ""
                        messagebox.showerror(
                            "Invalid value",
                            f"Row {orig_idx + 1}, feature '{f}'{u_txt}: "
                            f"'{txt}' is not a number.")
                        return

            if row["skin_var"] is not None:
                skin = row["skin_var"].get()
                skin_vals = SKIN_TYPE_MAP.get(skin, {"S_TCS": 0, "S_T_S": 0})
            else:
                skin = "—"
                skin_vals = {}

            canon_base = {}
            for f in self.mt_input_feats:
                c = canonical_of(f)
                if c:
                    canon_base[c] = base[f]

            derived = compute_derived_features(canon_base)

            feat_vals = {}
            for f in self.mt_all_feats:
                c = canonical_of(f)
                if c in DERIVED_FEATURES:
                    v = derived.get(c, np.nan)
                    if v is None or (isinstance(v, float) and np.isnan(v)):
                        v = 0.0
                    feat_vals[f] = float(v)
                elif c in SKIN_TYPE_FEATURES:
                    feat_vals[f] = float(skin_vals.get(c, 0))
                else:
                    feat_vals[f] = base.get(f, np.nan)

            rows_data.append({
                "orig_idx":  orig_idx,
                "base":      base,
                "skin":      skin,
                "derived":   derived,
                "feat_vals": feat_vals,
            })

        X = pd.DataFrame([r["feat_vals"] for r in rows_data],
                         columns=self.mt_all_feats)

        try:
            path = self._model_path(name)
            if path is None or not path.exists():
                raise FileNotFoundError(f"model file not found: {path}")
            model = self._load_model(name, path)
            y_pred = np.asarray(model.predict(X)).ravel()
        except Exception as e:
            messagebox.showerror("Predict error", f"{type(e).__name__}: {e}")
            return

        for k, r in enumerate(rows_data):
            orig_idx = r["orig_idx"]
            self.mt_rows[orig_idx]["pred_var"].set(f"{y_pred[k]:.4f}")

        res_data = {"#": [r["orig_idx"] + 1 for r in rows_data]}
        for f in self.mt_input_feats:
            res_data[f] = [r["base"].get(f, np.nan) for r in rows_data]
        if self.mt_skin_feats:
            res_data["Skin Type"] = [r["skin"] for r in rows_data]
        for f in self.mt_derived_feats:
            c = canonical_of(f)
            res_data[f] = [r["derived"].get(c, np.nan) for r in rows_data]
        res_data["Predicted_Nu"] = y_pred

        res = pd.DataFrame(res_data)
        self.mt_results_df = res
        self._populate_tree(self.mt_results_tree, res)
        self.mt_export_btn.config(state="normal")
        if self.mt_export_excel_btn is not None:
            self.mt_export_excel_btn.config(state="normal")

        self.mt_run_status_var.set(
            f"✓ Ran {len(rows_data)} row(s) — see values in the green boxes"
            + (f"  |  skipped {skipped_empty} empty row(s)" if skipped_empty else "")
        )

        self.log(f"==== Manual Test RUN (independent) ====")
        self.log(f"  model = {name}")
        self.log(f"  rows run = {len(rows_data)}  "
                 f"skipped empty = {skipped_empty}")
        self.log(f"  input = {self.mt_input_feats}  "
                 f"derived = {self.mt_derived_feats}  "
                 f"skin = {self.mt_skin_feats}")
        for _, r in res.iterrows():
            self.log(f"  row #{int(r['#'])}  pred = {r['Predicted_Nu']:.4f}")

    def _export_manual_results(self):
        if self.mt_results_df is None or self.mt_results_df.empty:
            messagebox.showwarning("Nothing to export",
                                   "Run a manual prediction first.")
            return
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        path = filedialog.asksaveasfilename(
            title="Save manual test results as CSV",
            defaultextension=".csv",
            initialfile=f"manual_test_{stamp}.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            self.mt_results_df.to_csv(path, index=False, encoding="utf-8-sig")
            messagebox.showinfo("Saved", f"CSV saved:\n{path}")
        except Exception as e:
            messagebox.showerror("Save error", str(e))

    def _export_manual_results_excel(self):
        """
        خروجی Excel برای Manual Test — دقیقاً با همان قواعد صفحه‌ی اصلی:
        - openpyxl
        - safe_sheet_name + جلوگیری از تکرار نام شیت (used)
        - شیت 'All_Predictions' + یک شیت به نام مدل
        - index=False
        """
        if self.mt_results_df is None or self.mt_results_df.empty:
            messagebox.showwarning("Nothing to export",
                                   "Run a manual prediction first.")
            return
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        path = filedialog.asksaveasfilename(
            title="Save manual test results as Excel (multi-sheet)",
            defaultextension=".xlsx",
            initialfile=f"manual_test_{stamp}.xlsx",
            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            self.log(f"==== Exporting Manual Test Excel: {path}")
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                used = set()

                def _w(df_, base):
                    name = safe_sheet_name(base)
                    orig, i = name, 1
                    while name in used:
                        sfx = f"_{i}"
                        name = (orig[:31 - len(sfx)]) + sfx
                        i += 1
                    used.add(name)
                    df_.to_excel(writer, sheet_name=name, index=False)
                    self.log(f"  wrote sheet: {name}  shape={df_.shape}")

                # 1) شیت کلی — هم‌نام با شیت صفحه‌ی اصلی
                _w(self.mt_results_df.reset_index(drop=True),
                   "All_Predictions")

                # 2) یک شیت به نام مدل (هم‌ساختار با صفحه‌ی اصلی)
                model_name = self.mt_model_var.get()
                if model_name:
                    sheet_df = self.mt_results_df.reset_index(drop=True).copy()
                    _w(sheet_df, model_name)

            messagebox.showinfo(
                "Saved",
                f"Manual Test Excel saved:\n{path}\n\n"
                f"Sheets written: {len(used)}\n"
                f"Model: {self.mt_model_var.get()}")
        except Exception as e:
            self.log(traceback.format_exc())
            messagebox.showerror("Save error", f"{type(e).__name__}: {e}")

    # -----------------------------------------------------
    # Export (multi-model predictions)
    # -----------------------------------------------------
    def export_results(self, fmt):
        if self.output_df is None or self.predictions_df is None \
                or self.predictions_df.empty:
            messagebox.showwarning("Nothing to export",
                                   "Run a prediction first.")
            return
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"

        if fmt == "xlsx":
            path = filedialog.asksaveasfilename(
                title="Save as Excel (multi-sheet)",
                defaultextension=".xlsx",
                initialfile=f"predictions_{stamp}.xlsx",
                filetypes=[("Excel", "*.xlsx")])
            if not path:
                return
            try:
                self.log(f"==== Exporting Excel: {path}")
                with pd.ExcelWriter(path, engine="openpyxl") as writer:
                    used = set()
                    def _w(df_, base):
                        name = safe_sheet_name(base)
                        orig, i = name, 1
                        while name in used:
                            sfx = f"_{i}"
                            name = (orig[:31 - len(sfx)]) + sfx
                            i += 1
                        used.add(name)
                        df_.to_excel(writer, sheet_name=name, index=False)
                        self.log(f"  wrote sheet: {name}  shape={df_.shape}")

                    _w(self.predictions_df.reset_index(drop=True),
                       "All_Predictions")

                    base_df = self.df_new.reset_index(drop=True)
                    for m in self.success_models:
                        pcol = f"{m}_Pred"
                        if pcol not in self.predictions_df.columns:
                            continue
                        sheet_df = base_df.copy()
                        sheet_df[pcol] = self.predictions_df[pcol].values
                        _w(sheet_df, m)

                messagebox.showinfo(
                    "Saved",
                    f"Multi-sheet Excel saved:\n{path}\n\n"
                    f"Sheets written: {len(used)}\n"
                    f"Models: {self.success_models}")
            except Exception as e:
                self.log(traceback.format_exc())
                messagebox.showerror("Save error", f"{type(e).__name__}: {e}")
        else:
            path = filedialog.asksaveasfilename(
                title="Save as CSV",
                defaultextension=".csv",
                initialfile=f"predictions_{stamp}.csv",
                filetypes=[("CSV", "*.csv")])
            if not path:
                return
            try:
                self.output_df.to_csv(path, index=False, encoding="utf-8-sig")
                messagebox.showinfo("Saved", f"CSV saved:\n{path}")
            except Exception as e:
                messagebox.showerror("Save error", str(e))

    # -----------------------------------------------------
    def _toggle_fullscreen(self, event=None):
        """F11 : ورود/خروج از fullscreen با DPI بومی مانیتور."""
        self._is_fullscreen = not self._is_fullscreen
        self.attributes("-fullscreen", self._is_fullscreen)
        # اگر مانیتور عوض شد، DPI را دوباره از مانیتور فعال بگیر
        try:
            dpi = DPI_OVERRIDE if DPI_OVERRIDE else _get_monitor_dpi()
            self._monitor_dpi = dpi
            self.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass
        return "break"

    def _exit_fullscreen(self):
        self._is_fullscreen = False
        self.attributes("-fullscreen", False)

    def _log_dpi_info(self):
        self.log(f"[DPI] monitor DPI = {self._monitor_dpi}  "
                 f"(scaling = {self._monitor_dpi / 72.0:.3f})  "
                 f"| Press F11 for fullscreen, Esc to exit.")

    # -----------------------------------------------------
    def _set_progress(self, value):
        self.after(0, lambda: self.progress.configure(value=value))

    def _set_status(self, text):
        self.after(0, lambda: self.status_var.set(text))


# =========================================================
if __name__ == "__main__":
    app = PredictorApp()
    app.mainloop()