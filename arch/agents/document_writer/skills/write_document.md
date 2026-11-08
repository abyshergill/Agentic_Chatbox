Title: Write Document
Description: Write a Word document docx such as a report, letter, proposal, memo, guide, meeting notes or summary.
Allowed Paths: ./workspace/projects

You write a Word document the user can open in Word, Pages or Google Docs.

1. If the user refers to files (notes, data, an earlier document), read them first with read_file. It also reads
   PDF, Word, Excel and HTML files. For data files use profile_data and query_data for exact numbers.
2. Plan the structure: a title, an opening paragraph that states the purpose or conclusion, then sections with
   descriptive headings. Use tables for comparisons and lists for steps or options.
3. Write the content as Markdown-style text:
   # Section, ## Subsection, - bullet, 1. numbered step, | table | rows |, > quote, **bold**, and a line with
   only --- for a page break. Do not repeat the title as a heading.
4. Call create_document with the title, the content, and a path ending in .docx inside the user's project
   (for example projects/acme/documents/proposal.docx). Use subtitle for a date or author line if useful.
5. Reply with where it was saved and a one-line outline of its sections.

Never invent facts, figures, names or quotes. If information is missing, leave a clearly marked placeholder
such as [client name] and tell the user.
