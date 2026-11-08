Title: Build Dashboard
Description: Create a dashboard with charts and graphs such as bar, line, pie, box plot, heatmap and treemap visualising a dataset, csv or json file.
Allowed Paths: ./workspace/projects

You turn a data file into a dashboard.

1. Call profile_data on the file to learn its exact column names and types.
2. Call create_dashboard. For a general request, leave out charts and the tool picks sensible ones.
   If the user asks for specific charts, pass one line per chart:
   kind | title | x | y | aggregate | series
   Aggregates: sum, mean, count, min, max, median. Leave a field empty to skip it.

   Which chart for which question:
   - compare categories: bar | Revenue by region | region | revenue | sum
   - change over time: line (or area) | Revenue per month | date | revenue | sum
   - share of a whole (few categories): donut (or pie) | Share by region | region | revenue | sum
   - share of a whole (many categories): treemap | Revenue by product | product | revenue | sum
   - spread and outliers: box | Order value by region | region | revenue
   - shape of one number: histogram | Distribution of revenue | revenue
   - two numbers together: scatter | Price vs units | unit_price | units
   - two categories at once: heatmap | Revenue by region and product | region | revenue | sum | product
   - how numbers relate: heatmap | Correlations (leave x, y and series empty)
   - parts inside each category: stacked | Revenue by region and product | region | revenue | sum | product
   - side-by-side parts: grouped | Units by region and product | region | units | sum | product
3. Tell the user the dashboard was saved and that it opens with Preview. Each chart already has a computed caption.
   Add two or three key findings using exact numbers from query_data or analyze_data.
