# My File (with a dot in the name)

This file demonstrates that a markdown file can have a dot in its filename.

## Section 1: Why Dots in Filenames?

Dots in filenames are perfectly valid in most modern operating systems, including:

- **Linux / macOS**: The dot is just another character. There is no special meaning except for hidden files (when the first character is a dot).
- **Windows**: Dots are allowed, though the final extension is used by the system to determine file type.
- **Web servers**: URLs often contain dots (e.g., `api.example.com/v1/resource.json`).

### Common Use Cases

1. **Versioned files**: `report.v2.md`, `config.prod.yaml`
2. **Descriptive names**: `my.draft.notes.md`, `data.2024.backup.csv`
3. **Compound extensions**: `.tar.gz`, `.spec.ts`

## Section 2: Markdown Features

This file also serves as a quick reference for Markdown syntax:

### Blockquotes []

> "The dot is mightier than the slash." 
> — Some sysadmin, probably

### Code Blocks

```python
def count_dots(filename: str) -> int:
    return filename.count(".")
```

### Tables

| Filename        | Ext  | Dots |
|-----------------|------|------|
| my.file.md      | .md  | 2    |
| config.yaml     | .yaml| 1    |
| notes.txt       | .txt | 1    |
| .gitignore      | none | 1    |

### Lists

- Item one
- Item two
- Item three
  - Nested item A
  - Nested item B

### Horizontal Rule

---

## Section 3: Fun Facts

| Fact | Detail |
|------|--------|
| Longest filename on Linux | 255 bytes |
| Max dots in a single filename | No limit, but be reasonable |
| Hidden files on Unix | Start with a single dot: `.hidden` |

## Section 4: Conclusion

Dots are just characters. Don't be afraid to use them — but do keep your filenames readable and your extensions correct. This file proves that a markdown file with a dot in its name works just fine on any system.

*End of file.*
