# Needs: exp, log, floor

def continuous_growth(principal: float, rate: float, years: float) -> float:
    return principal * exp(rate * years)


def years_to_double(rate: float) -> float:
    return log(2.0) / rate


def main() -> int:
    print("1000 at 5% for 10y :", round(continuous_growth(1000.0, 0.05, 10.0), 4))
    print("1000 at 7% for 10y :", round(continuous_growth(1000.0, 0.07, 10.0), 4))
    print("doubling time at 5%:", round(years_to_double(0.05), 4))
    print("doubling time at 7%:", round(years_to_double(0.07), 4))
    # Python's floor() returns an int; the compiler types every unknown
    # call as float (the safe widest choice), so int() makes the
    # narrowing explicit. int() is a static builtin, not an AI call.
    print("whole years at 7%  :", int(floor(years_to_double(0.07))))
    return 0
