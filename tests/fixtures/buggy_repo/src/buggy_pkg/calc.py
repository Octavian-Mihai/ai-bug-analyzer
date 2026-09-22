def sum_first_n(numbers: list[int], n: int) -> int:
    """Sums the first n elements of numbers.

    Deliberately buggy fixture: the range should be range(n), not range(n + 1),
    which reads one element past the intended window and raises IndexError
    whenever n == len(numbers).
    """
    total = 0
    for i in range(n + 1):
        total += numbers[i]
    return total


def is_even(value: int) -> bool:
    return value % 2 == 0
