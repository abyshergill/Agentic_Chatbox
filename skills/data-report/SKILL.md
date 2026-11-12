---
name: data-report
description: Analyze a CSV, Excel or JSON dataset and write a short report with exact figures, trends and a chart-ready summary. Use when the user asks what a dataset shows, wants insights, a summary report, or KPIs from a data file.
---

# Data report

Every number in the report must come from a tool result, never from estimation.

## Steps

1. Profile the file first (`profile_data`): columns, types, row count, missing values.
2. Decide the 3–5 questions the data can answer (totals, trends over time, top/bottom segments, outliers).
3. Answer each with `query_data` / `analyze_data`. Keep the exact figures.
4. If the user wants visuals, build a dashboard or Excel workbook with the data tools.

## Report format

```markdown
# <Dataset name>: key findings

**Rows:** … · **Period:** … · **Source:** <path>

## Headline
One or two sentences with the single most important finding.

## Findings
1. **<finding>** — figure, comparison, and why it matters.
2. …

## Data quality
Missing values, duplicates or suspicious values that affect the conclusions.

## Next questions
What would be worth checking next.
```

Never modify the source data file. Save outputs (reports, cleaned copies) as new files in the selected project.
