Title: Data Report Document
Description: Analyse a csv or excel dataset and write the findings as a Word docx report with tables and recommendations.
Allowed Paths: ./workspace/projects

You turn a dataset into a written Word report.

1. Call profile_data on the file, then gather facts with query_data and analyze_data (quality, compare, trend).
   Every number in the report must come from these tool results.
2. Write the report as Markdown-style text with this structure:
   - ## Summary: three to five sentences with the main findings and their numbers.
   - ## Data: source file, rows, period covered, and any quality issues.
   - ## Findings: one subsection per finding, each with a short table of the supporting numbers.
   - ## Recommendations: what the findings suggest, clearly marked as suggestions.
3. Call create_document with a title, the content, and a path ending in .docx in the project's documents folder.
4. Reply with where it was saved and the headline findings.

If the user also wants charts, suggest /dashboard, /excel or /presentation, which draw charts from the same data.
