"""
Allowance arithmetic: pension carry-forward and the £100k adjusted-income
taper.

Ported unchanged from the 8050 dashboard - these encode HMRC rules rather
than presentation, so the port is deliberately literal. No Dash imports and no
SQL, so the same functions serve the page and can be checked directly:

    from core import allowances as alw
    alw.carry_forward(data, 'ahmet', years, limits, rates, p11d)

Two rules worth stating, because they are easy to misread in the table:

  SIPP contributions are entered net and grossed at 1.25 for both the annual
  allowance and the adjusted-income calculation.

  Carry-forward consumes the current year's allowance first, then unused
  allowance from the previous three years oldest-first, which is the order
  HMRC applies and the order that maximises what stays available.
"""

from __future__ import annotations

SIPP_GROSS = 1.25
TAPER_THRESHOLD = 100_000


def car_bik_value(tax_year, rates, p11d) -> float:
    return round(float(p11d or 0) * float(rates.get(tax_year, 0) or 0))


def carry_forward(person_data, person, years, pension_limits, bik_rates,
                  p11d) -> dict:
    """
    {tax_year: {allowance, carry_fwd, available, total_used, remaining, ...}}

    years must be in ascending order - carry-forward is path dependent, so
    the sequence is part of the calculation rather than just display order.
    """
    results = {}
    unused_by_year = {}

    for yr in years:
        data = person_data.get(person, {}).get(yr, {})
        allowance = float(pension_limits.get(yr, 60000) or 60000)

        employer_pension = data.get("employer_pension", 0) or 0
        employee_pension = data.get("employee_pension", 0) or 0
        sipp_done = data.get("sipp_done", 0) or 0
        sipp_future = data.get("sipp_future", 0) or 0
        sipp_gross_done = sipp_done * SIPP_GROSS
        sipp_gross_fut = sipp_future * SIPP_GROSS
        total_used = (employer_pension + employee_pension
                      + sipp_gross_done + sipp_gross_fut)

        prev_3 = [y for y in years if y < yr][-3:]
        carry_fwd = sum(unused_by_year.get(y, 0) for y in prev_3)
        available = allowance + carry_fwd

        # Current year first, then oldest carry-forward.
        remaining_to_use = total_used
        current_unused = max(0, allowance - remaining_to_use)
        remaining_to_use = max(0, remaining_to_use - allowance)

        carry_pool = {y: unused_by_year.get(y, 0) for y in prev_3}
        for y in sorted(carry_pool.keys()):
            if remaining_to_use <= 0:
                break
            consumed = min(carry_pool[y], remaining_to_use)
            carry_pool[y] -= consumed
            remaining_to_use -= consumed
            unused_by_year[y] = carry_pool[y]

        unused_by_year[yr] = current_unused

        car_sacrifice = data.get("car_sacrifice", 0) or 0
        results[yr] = {
            "allowance": allowance,
            "carry_fwd": carry_fwd,
            "available": available,
            "employer_pension": employer_pension,
            "employee_pension": employee_pension,
            "sipp_done": sipp_done,
            "sipp_future": sipp_future,
            "sipp_gross_done": sipp_gross_done,
            "sipp_gross_future": sipp_gross_fut,
            "total_used": total_used,
            "remaining": max(0, available - total_used),
            "unused": current_unused,
            "salary": data.get("salary", 0) or 0,
            "bonus": data.get("bonus", 0) or 0,
            "car_sacrifice": car_sacrifice,
            # BIK only bites if the car is actually taken that year.
            "car_bik": (car_bik_value(yr, bik_rates, p11d)
                        if car_sacrifice else 0),
            "other_deductions": data.get("other_deductions", 0) or 0,
            "isa": data.get("isa", 0) or 0,
        }
    return results


def adjusted_income(salary, bonus, car_sacrifice, car_bik, employee_pension,
                    other_deductions, sipp_done_gross, sipp_future_gross):
    """
    Returns (adjusted, gap_to_100k, additional_net_sipp_to_close_gap).

    BIK adds to income; salary sacrifice and pension contributions come off.
    """
    adjusted = (salary + bonus
                + car_bik
                - car_sacrifice
                - employee_pension
                - other_deductions
                - sipp_done_gross
                - sipp_future_gross)
    gap = max(0, adjusted - TAPER_THRESHOLD)
    return adjusted, gap, gap / SIPP_GROSS
