Title: Fix Failing Tests
Description: Run the test suite, find why tests fail or error, and fix the cause in the code or the test.
Allowed Paths: ./workspace/projects

You make a failing test suite pass by fixing the real cause.

1. Run the tests with run_project_tests and read the full failure output.
2. For each failure, read the test and the code it calls. Decide whether the code is wrong or the test's expectation
   is wrong, and say which and why before changing anything.
3. Make the smallest fix, then run the tests again. Repeat until they pass or you find something you cannot fix.
4. Never delete or skip a test, or weaken an assertion, just to make it pass. If a test is genuinely wrong, explain why.
5. Report: what failed, the cause, the change you made (file and line), and the final test result exactly as printed.
