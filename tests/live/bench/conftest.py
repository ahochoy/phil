# The fixture repos carry their own tests (py-calc's tests/test_calc.py): they are the
# benchmark's inputs, run inside a copied repo, never collected by Phil's own suite.
collect_ignore = ["fixtures"]
