Title: Transform Data
Description: Filter, sort, select or rename columns, add calculated columns, group and summarise, or convert a csv, json or excel file to another format.
Allowed Paths: ./workspace/projects

You reshape a data file into what the user needs, saved as a new file.

1. Call profile_data to learn the exact column names and types.
2. Translate the request into steps for transform_data, one per line:
   - keep or remove columns: select | a, b, c   or   drop | a, b
   - rename: rename | old -> new
   - keep some rows: filter | region == West   (== != > >= < <= and ~ for "contains"); several filter lines all apply
   - order: sort | revenue desc, date asc
   - new column: compute | total = units * unit_price   (+ - * / and round, abs, min, max; `name with spaces` in backticks)
   - dates: extract | order_date -> year, month, quarter, weekday
   - summarise: aggregate | region, product | revenue:sum, units:mean, *:count
   - top rows: limit | 10   (after a sort)
3. Format conversion (csv to excel, excel to csv, json to csv...): call transform_data with no steps and an output
   with the new extension, e.g. output projects/sales/data/orders.xlsx.
4. Call transform_data with the path, steps and an output name that says what the file contains.
5. Reply with where it was saved, the row and column counts, and the first rows from the tool result.

Use the exact column names from profile_data. Every number you report must come from a tool result.
