Title: Build Workbook
Description: Create an excel workbook, xlsx report or spreadsheet from data, with a summary sheet, pivot tables, totals, groups, trends and native excel charts.
Allowed Paths: ./workspace/projects

You build a well-organised Excel workbook from a data file.

1. Call profile_data to learn the columns (pass sheet for an Excel source with several sheets).
2. Choose the extra sheets that answer the user's question (the workbook always has a Summary sheet and the full data
   as a filterable Excel table):
   - pivot | rows | columns | metric | aggregate     e.g. pivot | region | product | revenue | sum
   - group | category | metric                      e.g. group | region | revenue  (count, sum, share, mean, median per group)
   - trend | metric | aggregate | period            e.g. trend | revenue | sum | month  (needs a date column)
   - quality                                         a data quality check
3. Add two to four charts as lines: kind | title | x | y | aggregate | series
   Kinds that become native Excel charts: bar, line, area, pie, donut, histogram, scatter, stacked, grouped.
   box and heatmap become tables with colour scales.
4. If the user wants one tab per region, customer or other category, pass split_by with that column.
5. Call create_excel with the path, a title, the sheets, the charts, and split_by if needed.
6. Reply with where the workbook was saved, the list of sheets, and one line on what each sheet shows.
   Mention that totals and changes are Excel formulas, so they update if numbers are edited.
