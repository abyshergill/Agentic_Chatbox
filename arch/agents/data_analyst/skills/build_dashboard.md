# Build Dashboard

Title: Build Dashboard

Description: Builds a dashboard of charts from a dataset (CSV, TSV, Excel, or JSON). Supports bar, line, area, pie, donut, treemap, box plot, histogram, scatter, heatmap, stacked, and grouped charts. Use it when the user asks to visualize, chart, graph, or build a dashboard from their data. It never changes the source file.

Allowed Paths: ./workspace/projects

## Instructions

You turn a data file into a clear, accurate dashboard, then summarize what it shows using exact numbers.

### Rules

1. Never modify the source data file. The dashboard is always a new file.
2. Stay inside `./workspace/projects`. If the user points to a file outside this path, say so and ask for a file inside it.
3. Use only exact column names from the `profile_data` result. The column names in the examples below (`region`, `revenue`, and so on) are placeholders; always replace them with real columns.
4. Every number you write in the reply comes from `query_data` or `analyze_data`. Never estimate, and never quote numbers read off a chart.

### Step 1: Find the file

* If the user names a file or has one open, use it.
* If no file is named, call `list_directory` on the project's data folder, show the data files found, and ask which one to use. Do not pick one yourself.
* If the named file is not found, suggest the closest matching file names and ask the user to confirm.

### Step 2: Profile it

* Call `profile_data` before anything else (once per file per conversation, unless the file has changed). For Excel files, pass `sheet` if the data is not on the first sheet.
* From the profile, note which columns are categorical, numeric, and date or time, how many distinct values each category has, and any missing values or type problems. You need this to choose and check charts.

### Step 3: Decide which charts to build

* General request ("make a dashboard of this file"): call `create_dashboard` without `charts`. The tool picks sensible charts.
* Specific request ("show revenue by region as a bar chart and a monthly trend"): pass `charts`, one line per chart, in this format:

```
kind | title | x | y | aggregate | series
```

* `aggregate` is one of: `sum`, `mean`, `count`, `min`, `max`, `median`.
* Leave a field empty to skip it. Trailing empty fields can be left out.
* Write titles that say what the chart shows, for example "Revenue by region" rather than "Chart 1".

Choose the chart by the question the user wants answered:

* Compare categories (x categorical, y numeric):
  `bar | Revenue by region | region | revenue | sum`
* Change over time (x date or time, y numeric). Use `line` or `area`:
  `line | Revenue per month | date | revenue | sum`
* Share of a whole, few categories (6 or fewer, positive values). Use `donut` or `pie`:
  `donut | Share by region | region | revenue | sum`
* Share of a whole, many categories (positive values):
  `treemap | Revenue by product | product | revenue | sum`
* Spread and outliers by group (x categorical, y numeric, no aggregate):
  `box | Order value by region | region | revenue`
* Shape of one number (x numeric only):
  `histogram | Distribution of revenue | revenue`
* Two numbers together (x and y numeric, no aggregate):
  `scatter | Price vs units | unit_price | units`
* Two categories at once (x and series categorical, y numeric):
  `heatmap | Revenue by region and product | region | revenue | sum | product`
* How numeric columns relate (leave x, y, and series empty):
  `heatmap | Correlations`
* Parts inside each category (series with few distinct values):
  `stacked | Revenue by region and product | region | revenue | sum | product`
* Parts side by side (series with few distinct values):
  `grouped | Units by region and product | region | units | sum | product`

Check every chart line against the profile before calling:

* Each column named in the line exists, with that exact spelling.
* The column types match what the chart needs (shown in parentheses above). Do not sum or average a text column; use `count` instead.
* The aggregate makes sense: `sum` for totals such as revenue or units, `mean` or `median` for typical values such as price or cycle time, `count` for how many rows.
* Pie and donut charts have 6 slices or fewer; otherwise use `bar` or `treemap`.
* A `series` with many distinct values (more than about 8) makes stacked, grouped, and heatmap charts unreadable. Choose a different column or ask the user.
* Aim for 4 to 8 charts. Start with the chart that answers the user's main question.

If a chart the user asked for does not fit the data (for example a line chart with no date column), say so briefly and suggest the closest chart that does fit. If what the user wants is unclear in a way that changes the dashboard (which metric, which grouping), ask one short question first.

### Step 4: Build it

* Call `create_dashboard` with the file and, if needed, the chart lines.
* If the call fails, read the error, fix the cause (wrong column name, wrong type, bad line format), and retry once. If it still fails, tell the user which chart failed and why.
* To change an existing dashboard, call `create_dashboard` again with the full, updated chart list.

### Step 5: Reply

1. Confirm the dashboard was saved, give the exact file path from the tool result, and say it opens with Preview.
2. List the charts it contains in one short line. Each chart already has a computed caption, so do not repeat the captions.
3. Add two or three key findings. Get each number from `query_data` or `analyze_data` first, for example the top category and its share, the change between the first and last period, or a notable outlier. Each finding is one sentence: the number, then what it means.
4. Mention any caveat that affects what the charts show, such as missing values in a charted column, excluded rows, or dates that did not parse.

Keep the reply short. Do not paste chart specs or raw tool output unless the user asks for them.
