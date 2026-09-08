"""
Makes `import groundwork...` work inside tests.

pytest automatically adds the folder containing this file to Python's import
path. Because this file sits at the project root, every test can import the
`groundwork` package without us having to install the project as a library.

There is nothing else in here on purpose. Shared test fixtures belong in
`tests/conftest.py`, next to the tests that use them.
"""
