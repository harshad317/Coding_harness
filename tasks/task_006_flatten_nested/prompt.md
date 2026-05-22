# Task: flatten_nested

Implement the function `flatten(nested)` in `solution.py`.

## Spec
- Input: a list that may contain integers, strings, or other lists, nested to any depth.
- Output: a new flat list containing every non-list element in left-to-right order.
- Empty lists contribute nothing.
- Do not flatten strings — treat each string as a single atom.
- Do not mutate the input.

## Examples
```
flatten([1, [2, 3], 4])               == [1, 2, 3, 4]
flatten([1, [2, [3, [4]]]])           == [1, 2, 3, 4]
flatten([])                           == []
flatten([[], [], []])                 == []
flatten(["a", ["b", ["c"]]])          == ["a", "b", "c"]
flatten([1, [], [2, []], 3])          == [1, 2, 3]
```

A starter file `solution.py` is provided. Write self-tests in `self_tests.py`; the harness will run them and keep hidden tests private.
