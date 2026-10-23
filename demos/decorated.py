# Ordinary Python, outside the subset in six ways at once: a decorator
# factory, a closure, a class with a property and a staticmethod, a list
# comprehension, a tuple, and f-strings.
#
# Not one of them changes what the program prints, which is the whole point.
# None of them has to survive the rewrite - the output does.
#
#     python -m src.driver demos/decorated.py --repair --run


def traced(label):
    def decorate(function):
        def wrapper(self, amount):
            result = function(self, amount)
            print(f"{label} {amount} -> {result}")
            return result
        return wrapper
    return decorate


class Account:
    RATE = 20

    def __init__(self, balance):
        self._balance = balance

    @property
    def balance(self):
        return self._balance

    @traced("deposit")
    def deposit(self, amount):
        self._balance += amount
        return self._balance

    @staticmethod
    def interest(amount):
        return amount // Account.RATE


def main() -> int:
    account = Account(100)
    for amount in (25, 40, 35):
        account.deposit(amount)
    print(f"balance = {account.balance}")

    earned = [Account.interest(x) for x in (100, 200, 300)]
    for value in earned:
        print("interest", value)
    return 0
