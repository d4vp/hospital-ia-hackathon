"""Decision-support reports based on inferential statistics (scipy.stats).

Every section returns the numbers AND a plain-language conclusion.

1. ER waiting time: 95% t-interval for the mean + bootstrap interval for the median.
2. Occupancy by bed group: 95% t-interval of the mean daily occupancy (last 30 days).
   Caveat: daily values are autocorrelated, so the interval is optimistic (narrower).
3. Period comparison: ER wait this month vs previous month. Mann-Whitney U
   (non-parametric: waits are skewed) + Welch t-test as reference, with effect size r.
4. Trends: weekly admissions over the last 8 complete weeks by diagnosis chapter and
   by bed group; ordinary least squares (scipy.stats.linregress). A significant positive
   slope produces a predictive alert with a one-week forecast.
5. Root cause of waiting time: day vs night (Mann-Whitney), triage levels
   (Kruskal-Wallis) and daily volume vs wait (Spearman).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from app.core.config import settings
from app.core.i18n import chapter_label, fmt_date, fmt_number, t
from app.services import analytics as an
from app.services.analytics import Frames

ALPHA = 0.05
TREND_WEEKS = 8
MIN_WEEKLY_MEAN = 5  # ignore categories with very few admissions per week


def _p(value: float, lang: str) -> str:
    """'p < 0,001' / 'p = 0,013' formatted for the language."""
    if value < 0.001:
        return "p < 0,001" if lang == "es" else "p < 0.001"
    return f"p = {fmt_number(value, lang, 3)}"


def _t_interval(values: np.ndarray) -> tuple[float, float, float]:
    mean = float(np.mean(values))
    sem = stats.sem(values)
    low, high = stats.t.interval(0.95, len(values) - 1, loc=mean, scale=sem)
    return mean, float(low), float(high)


def _effect_size_label(r: float, lang: str) -> str:
    r = abs(r)
    key = "effect_negligible" if r < 0.1 else "effect_small" if r < 0.3 else "effect_medium" if r < 0.5 else "effect_large"
    return t(key, lang)


# --------------------------------------------------------------------------- #
def wait_confidence_interval(frames: Frames, lang: str, days: int = 30) -> dict:
    adm = frames.admissions
    recent = adm[adm["admission_date"] >= frames.reference_date - pd.Timedelta(days=days)]
    waits = an.er_waits(recent).to_numpy()
    if len(waits) < 30:
        return {"conclusion": t("rep_insufficient", lang)}
    mean, low, high = _t_interval(waits)
    boot = stats.bootstrap((waits,), np.median, confidence_level=0.95, n_resamples=2000,
                           method="percentile", random_state=np.random.default_rng(7))
    median = float(np.median(waits))
    mlow, mhigh = float(boot.confidence_interval.low), float(boot.confidence_interval.high)
    return {
        "n": int(len(waits)), "mean": round(mean, 1), "ci95_mean": [round(low, 1), round(high, 1)],
        "median": round(median, 1), "ci95_median": [round(mlow, 1), round(mhigh, 1)],
        "skewness": round(float(stats.skew(waits)), 2),
        "conclusion": t("rep_wait_ci", lang, days=days, mean=fmt_number(mean, lang), low=fmt_number(low, lang),
                        high=fmt_number(high, lang), n=len(waits), median=fmt_number(median, lang),
                        mlow=fmt_number(mlow, lang), mhigh=fmt_number(mhigh, lang)),
    }


def occupancy_confidence_intervals(frames: Frames, lang: str, days: int = 30) -> list[dict]:
    end = frames.reference_date.normalize() - pd.Timedelta(days=1)  # last complete day
    start = end - pd.Timedelta(days=days - 1)
    trend = an.occupancy_trend(frames, start, end + pd.Timedelta(hours=23))
    by_group = pd.DataFrame(trend["by_group"])
    if by_group.empty:
        return []
    threshold = settings.ALERT_OCCUPANCY_THRESHOLD_PCT
    results = []
    for group, sub in by_group.groupby("bed_group"):
        values = sub["occupancy_pct"].dropna().to_numpy(dtype=float)
        if len(values) < 7 or np.std(values) == 0:
            continue
        mean, low, high = _t_interval(values)
        verdict_key = "rep_occupancy_over" if low > threshold else "rep_occupancy_under" if high < threshold else "rep_occupancy_mixed"
        results.append({
            "bed_group": group, "days": int(len(values)), "mean_pct": round(mean, 1),
            "ci95": [round(low, 1), round(high, 1)],
            "conclusion": t("rep_occupancy_ci", lang, group=group, mean=fmt_number(mean, lang), days=len(values),
                            low=fmt_number(low, lang), high=fmt_number(high, lang),
                            verdict=t(verdict_key, lang, threshold=fmt_number(threshold, lang, 0))),
        })
    return sorted(results, key=lambda r: -r["mean_pct"])


def compare_months(frames: Frames, lang: str) -> dict:
    ref = frames.reference_date
    cur_start = ref.normalize().replace(day=1)
    prev_start = (cur_start - pd.Timedelta(days=1)).replace(day=1)
    adm = frames.admissions
    current = an.er_waits(adm[(adm["admission_date"] >= cur_start) & (adm["admission_date"] <= ref)]).to_numpy()
    previous = an.er_waits(adm[(adm["admission_date"] >= prev_start) & (adm["admission_date"] < cur_start)]).to_numpy()
    if len(current) < 20 or len(previous) < 20:
        return {"conclusion": t("rep_insufficient", lang)}
    u_stat, p_mw = stats.mannwhitneyu(current, previous, alternative="two-sided")
    t_stat, p_t = stats.ttest_ind(current, previous, equal_var=False)
    # Effect size r = Z / sqrt(N), Z recovered from the two-sided p-value.
    z = stats.norm.isf(p_mw / 2) if p_mw > 0 else 8.0
    r = float(z / np.sqrt(len(current) + len(previous)))
    cur_median, prev_median = float(np.median(current)), float(np.median(previous))
    if p_mw < ALPHA:
        key = "rep_compare_sig_up" if cur_median > prev_median else "rep_compare_sig_down"
        verdict = t(key, lang, effect=t("rep_effect", lang, r=fmt_number(r, lang, 2), size=_effect_size_label(r, lang)))
    else:
        verdict = t("rep_compare_ns", lang)
    cur_label = f"{fmt_date(cur_start, lang)}–{fmt_date(ref, lang)}"
    prev_label = f"{fmt_date(prev_start, lang)}–{fmt_date(cur_start - pd.Timedelta(days=1), lang)}"
    return {
        "current": {"label": cur_label, "n": int(len(current)), "mean": round(float(current.mean()), 1), "median": round(cur_median, 1)},
        "previous": {"label": prev_label, "n": int(len(previous)), "mean": round(float(previous.mean()), 1), "median": round(prev_median, 1)},
        "mann_whitney": {"u": float(u_stat), "p_value": float(p_mw), "effect_size_r": round(r, 3)},
        "welch_t": {"t": round(float(t_stat), 3), "p_value": float(p_t)},
        "conclusion": t("rep_compare", lang, current_label=cur_label, cur_median=fmt_number(cur_median, lang),
                        n_cur=len(current), previous_label=prev_label, prev_median=fmt_number(prev_median, lang),
                        n_prev=len(previous), p=_p(p_mw, lang), verdict=verdict),
    }


def _weekly_counts(adm: pd.DataFrame, column: str, ref: pd.Timestamp, weeks: int) -> pd.DataFrame:
    """Admissions per complete 7-day window ending at the reference date (week 0 = oldest)."""
    start = ref - pd.Timedelta(days=7 * weeks)
    recent = adm[(adm["admission_date"] > start) & (adm["admission_date"] <= ref)].dropna(subset=[column])
    week = ((recent["admission_date"] - start) / pd.Timedelta(days=7)).astype(int).clip(upper=weeks - 1)
    table = recent.assign(week=week).groupby([column, "week"]).size().unstack(fill_value=0)
    return table.reindex(columns=range(weeks), fill_value=0)


def detect_trends(frames: Frames, lang: str) -> dict:
    results: list[dict] = []
    ref = frames.reference_date
    for column, kind in (("diagnosis_chapter", "diagnosis"), ("bed_group", "service")):
        weekly = _weekly_counts(frames.admissions, column, ref, TREND_WEEKS)
        x = np.arange(TREND_WEEKS)
        for category, row in weekly.iterrows():
            y = row.to_numpy(dtype=float)
            if y.mean() < MIN_WEEKLY_MEAN or np.all(y == y[0]):
                continue
            fit = stats.linregress(x, y)
            if fit.pvalue >= ALPHA:
                continue
            forecast = max(fit.intercept + fit.slope * TREND_WEEKS, 0)
            fitted_last = fit.intercept + fit.slope * (TREND_WEEKS - 1)
            pct = fit.slope / fitted_last * 100 if fitted_last > 0 else 0
            subject = chapter_label(category, lang) if kind == "diagnosis" else str(category)
            if fit.slope > 0:
                action_key = f"action_{category}" if f"action_{category}" in _ACTION_KEYS else "action_generic"
                text = t("rep_trend_up", lang, subject=subject, weeks=TREND_WEEKS, slope=fmt_number(fit.slope, lang),
                         p=_p(fit.pvalue, lang), r2=fmt_number(fit.rvalue ** 2, lang, 2), pct=fmt_number(pct, lang, 0),
                         forecast=fmt_number(forecast, lang, 0), action=t(action_key, lang))
            else:
                text = t("rep_trend_down", lang, subject=subject, slope=fmt_number(fit.slope, lang), p=_p(fit.pvalue, lang))
            results.append({
                "kind": kind, "category": category, "label": subject, "weekly_admissions": [int(v) for v in y],
                "slope_per_week": round(float(fit.slope), 2), "p_value": float(fit.pvalue),
                "r_squared": round(float(fit.rvalue ** 2), 3), "forecast_next_week": round(float(forecast), 1),
                "direction": "up" if fit.slope > 0 else "down", "conclusion": text,
            })
    results.sort(key=lambda r: (r["direction"] != "up", r["p_value"]))
    return {"weeks": TREND_WEEKS, "trends": results,
            "conclusion": None if results else t("rep_trend_none", lang)}


_ACTION_KEYS = {"action_respiratory", "action_injury", "action_pregnancy", "action_infectious"}


def root_cause_waits(frames: Frames, lang: str, days: int = 30) -> dict:
    adm = frames.admissions
    recent = adm[(adm["admission_date"] >= frames.reference_date - pd.Timedelta(days=days))
                 & (adm["admission_route"] == an.ER_ROUTE) & adm["wait_minutes"].notna()]
    if len(recent) < 50:
        return {"findings": [], "conclusion": t("rep_insufficient", lang)}
    findings: list[dict] = []

    day = recent.loc[recent["shift"] == "day", "wait_minutes"].to_numpy()
    night = recent.loc[recent["shift"] == "night", "wait_minutes"].to_numpy()
    if len(day) >= 10 and len(night) >= 10:
        _, p = stats.mannwhitneyu(day, night, alternative="two-sided")
        worse = "day" if day.mean() > night.mean() else "night"
        verdict = t("rep_root_shift_sig", lang, worse=t(f"shift_{worse}", lang)) if p < ALPHA else t("rep_root_shift_ns", lang)
        findings.append({"factor": "shift", "p_value": float(p), "day_mean": round(float(day.mean()), 1),
                         "night_mean": round(float(night.mean()), 1),
                         "conclusion": t("rep_root_shift", lang, day=fmt_number(day.mean(), lang),
                                         night=fmt_number(night.mean(), lang), p=_p(p, lang), verdict=verdict)})

    by_level = {int(lv): g["wait_minutes"].to_numpy() for lv, g in recent.dropna(subset=["triage_level"]).groupby("triage_level") if len(g) >= 5}
    if len(by_level) >= 2:
        _, p = stats.kruskal(*by_level.values())
        worst_level = max(by_level, key=lambda lv: by_level[lv].mean())
        share = len(by_level[worst_level]) / sum(len(v) for v in by_level.values()) * 100
        findings.append({"factor": "triage_level", "p_value": float(p), "worst_level": worst_level,
                         "means": {str(k): round(float(v.mean()), 1) for k, v in by_level.items()},
                         "conclusion": t("rep_root_triage", lang, p=_p(p, lang), level=worst_level,
                                         mean=fmt_number(by_level[worst_level].mean(), lang),
                                         share=fmt_number(share, lang, 0))})

    daily = recent.assign(day=recent["admission_date"].dt.normalize()).groupby("day")["wait_minutes"].agg(["size", "mean"])
    if len(daily) >= 10:
        rho, p = stats.spearmanr(daily["size"], daily["mean"])
        verdict = t("rep_root_volume_sig", lang) if (p < ALPHA and rho > 0) else t("rep_root_volume_ns", lang)
        findings.append({"factor": "daily_volume", "spearman_rho": round(float(rho), 3), "p_value": float(p),
                         "conclusion": t("rep_root_volume", lang, rho=fmt_number(rho, lang, 2), p=_p(p, lang), verdict=verdict)})
    return {"days": days, "findings": findings}


def build_inferential_report(frames: Frames, lang: str) -> dict:
    return {
        "reference_date": frames.reference_date.isoformat(),
        "wait_time_ci": wait_confidence_interval(frames, lang),
        "occupancy_ci": occupancy_confidence_intervals(frames, lang),
        "month_comparison": compare_months(frames, lang),
        "trends": detect_trends(frames, lang),
        "root_cause": root_cause_waits(frames, lang),
    }
