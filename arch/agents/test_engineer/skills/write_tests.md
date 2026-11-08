Title: Write Tests
Description: Write unit tests for code in a project, covering normal cases, edge cases and errors, then run them.
Allowed Paths: ./workspace/projects

You add tests for the code the user names.

1. Read the code under test with read_file, and look for existing tests (search_files for test_*.py, *.test.js,
   *_test.go) so you follow the project's framework, folder layout and style.
2. List the behaviours to test: the normal case, boundaries (empty, zero, one, very large), invalid input and the
   errors it should raise, and any bug the user mentioned.
3. Write the tests in a test file that matches the project's layout. Each test checks one behaviour and has a name
   that says what it checks.
4. Run them with run_project_tests (or execute_command with the project's test command).
5. Report the result exactly as the run printed it. If a test fails because the code is wrong, say so and show the
   failing case; do not change the code unless the user asked you to fix it.
