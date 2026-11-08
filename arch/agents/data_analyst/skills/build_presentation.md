Title: Build Presentation
Description: Create a presentation, slide deck or PowerPoint pptx with charts and key findings from a dataset, csv or json file.
Allowed Paths: ./workspace/projects

You turn a data file into a short, clear presentation.

1. Call profile_data on the file to learn its exact column names and types.
2. Gather the facts for the key findings slide. Use query_data and analyze_data (compare, trend, quality)
   so every number is exact. Aim for three to five findings.
3. Choose four to six charts that tell one story, in this order: the headline total or trend, the main breakdown,
   the spread or outliers, then one relationship. Pass one line per chart:
   kind | title | x | y | aggregate | series
   Kinds: bar, line, area, pie, donut, histogram, scatter, box, heatmap, stacked, grouped, treemap.
   Write chart titles as the message, for example "South earns the most revenue", not "Revenue by region".
   If the user did not ask for anything specific you may leave charts out and the tool picks them.
4. Call create_presentation with the path, a title, the charts, and the findings. Each finding is one sentence with
   an exact number. Leave format out to get both the HTML deck and the PowerPoint file. Use format pptx or html
   only if the user asks for just one.
5. Tell the user where the files were saved: the HTML deck opens with Preview and prints to PDF one slide per page;
   the .pptx opens in PowerPoint, Keynote or Google Slides with editable charts. List the slides in one line each.

Never put a number in a finding that did not come from a tool result.
