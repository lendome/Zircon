# Zircon Design System

## Philosophy

Dark-first, minimal, Windows-native aesthetic. Inspired by modern Windows 11 design principles: flat chrome, subtle glassmorphism, generous spacing, and clear typographic hierarchy. No emojis -- all icons are inline SVGs.

---

## 1. Color Palette

### Surface Colors

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#0a0a0a` | Primary background |
| `--bg-alt` | `#0e0e0e` | Secondary background (sidepanel, toolbars) |
| `--bg-card` | `#141414` | Card / elevated surface |
| `--bg-elev` | `#1a1a1a` | Elevated surface (active states) |
| `--bg-hover` | `#232323` | Hover state background |

### Border Colors

| Token | Value | Usage |
|-------|-------|-------|
| `--border` | `rgba(255, 255, 255, 0.1)` | Default subtle borders |
| `--border-light` | `rgba(255, 255, 255, 0.15)` | Elevated borders (modals, context menus) |

### Text Colors

| Token | Value | Usage |
|-------|-------|-------|
| `--text` | `#f5f5f5` | Primary text |
| `--text-dim` | `#c0c0c0` | Secondary text |
| `--text-muted` | `#888888` | Muted / disabled text |

### Semantic Colors

| Token | Value | Usage |
|-------|-------|-------|
| `--green` / `--green-light` | `#2e7d32` / `#66bb6a` | Success, online status |
| `--red` / `--red-light` | `#c62828` / `#ef5350` | Danger, errors, close button hover |
| `--yellow` / `--yellow-light` | `#9e9d24` / `#d4d454` | Warnings, running state |
| `--purple` / `--purple-light` | `#6a1b9a` / `#ab47bc` | Tool results, reasoning |

---

## 2. Typography

### Font Stack

```css
font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Helvetica Neue',
             'Segoe UI', Roboto, Arial, sans-serif;
```

Monospace fallback for code:

```css
font-family: 'JetBrains Mono', 'Consolas', 'Courier New', monospace;
```

### Font Sizes

| Size | Context |
|------|---------|
| 8px | Stat labels, tiny badges |
| 9px | Section labels, tab badges, session status |
| 10px | Sidebar items, toolbar text, mode badges, file tags |
| 11px | Action buttons, folder names, session tasks |
| 12px | Titlebar label, message body, modal titles, plan steps |
| 13px | Default body text, welcome subtitle |
| 14px | Headings (h2) |
| 15px | Headings (h1) |
| 20px | Context window value display |
| 22px | Welcome screen title |

### Line Height

- Default: `1.6`
- Monospace: `1.7`

### Rendering

```css
-webkit-font-smoothing: antialiased;
-moz-osx-font-smoothing: grayscale;
text-rendering: optimizeLegibility;
font-feature-settings: 'kern' 1, 'liga' 1;
```

---

## 3. Spacing & Sizing

### Spacing Scale (8px base)

| Token | Value |
|-------|-------|
| Sidepanel | 8px padding inside |
| Panel sections | 8px padding |
| Modal body | 16px padding |
| Output area | 12px / 16px horizontal |
| Gaps | 4px / 6px / 8px / 10px / 12px / 14px |

### Key Sizes

| Element | Size |
|---------|------|
| Titlebar height | 38px |
| Toolbar height | 32px |
| Sidepanel width | 250px |
| Activity panel width | 220px |
| Window control buttons | 46px wide, full height |
| Settings/titlebar buttons | 30px x 30px |
| Input area height | auto (flex, ~44px) |

---

## 4. Border Radius

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-sm` | 4px | Inputs, buttons, small cards |
| `--radius-md` | 6px | Cards, modals, panels |
| `--radius-lg` | 10px | Modals (outer container) |

---

## 5. Shadows

| Token | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.3)` | Subtle elevation |
| `--shadow-md` | `0 4px 12px rgba(0,0,0,0.4)` | Dropdowns, context menus |
| `--shadow-lg` | `0 8px 32px rgba(0,0,0,0.5)` | Modals |

---

## 6. Transitions

```css
--transition: 0.2s cubic-bezier(0.25, 0.1, 0.25, 1);
```

Hover and active states use instant transitions (`0.08s` or `0.1s`).

---

## 7. Glassmorphism

Applied via:

```css
backdrop-filter: blur(12px);
-webkit-backdrop-filter: blur(12px);
```

Used on:
- Titlebar (12px blur)
- Modals overlay (4px blur)
- Context menus (12px blur)

---

## 8. Components

### 8.1 Titlebar (Windows Style)

```
[Zircon]                                    [Settings] [_] [□] [X]
|-- drag region ------------------------------------- no-drag ----|
```

- Full width, 38px height
- `-webkit-app-region: drag` on the bar and label
- `-webkit-app-region: no-drag` on all buttons
- Label: 12px, font-weight 600, opacity 0.7
- Three window control buttons on the far right:
  - **Minimize**: SVG line, hovers `--bg-hover`
  - **Maximize**: SVG square outline, hovers `--bg-hover`, toggles between `maximize()` and `restore()`
  - **Close**: SVG X, hovers `#c42b1c` with white text

### 8.2 Sidepanel

- 250px fixed width
- Sections with transparent hover highlight
- Segmented controls (tier/mode selectors) with active fill
- iOS-style toggle switches (32px x 18px pill)
- Status grid (2-column, 4 cards)

### 8.3 Action Buttons

```css
.action-btn {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 7px 14px;
  font-size: 11px;
  font-weight: 500;
}
```

Variants: `.action-btn-secondary` (dim text), `.action-btn-danger` (red text/border)

### 8.4 Messages

- Left border accent (2px) with semantic coloring:
  - User: `--text-dim`
  - Agent: `--text`
  - Error: `--red-light`
  - Tool result: `--purple-light`
  - Trace: `--yellow`
  - Progress: `--yellow` (italic)
  - Reasoning: `--purple` (pulsing opacity)
  - Done: `--green-light` (bold)
- Label: 10px uppercase, 1px letter-spacing, 0.5 opacity
- Body: 12px JetBrains Mono

### 8.5 Modals

- Centered overlay with `backdrop-filter: blur(4px)`
- Max 80vh height, 640px width (700px for settings)
- Header: 14px padding, border-bottom
- Footer: 12px padding, border-top, right-aligned buttons
- Outer border-radius: 10px

### 8.6 Context Menu

- Fixed position, min-width 180px
- `backdrop-filter: blur(12px)`
- Items: 8px 14px padding, 12px font, 500 weight
- Divider: 1px at 10% opacity
- Danger variant: red text with red hover background

### 8.7 Scrollbars

```css
width: 4px;
scrollbar-color: rgba(255, 255, 255, 0.08) transparent;
thumb: 2px border-radius, hover becomes 15% opacity
```

---

## 9. Status Indicators

### Connection Dot

6px circle, colored by state:
- Offline: `#443030`
- Online: `--green-light` with green glow
- Running: `--yellow-light` with yellow glow

### Sub-agent Dot

6px circle, same semantic colors but smaller.

### Status Badges

9px uppercase text with colored border and tinted background:
- `status-completed`: green
- `status-running`: yellow
- `status-failed`: red
- `status-created`: muted

---

## 10. Segmented Controls

Tier and mode selectors use a segmented control pattern:

```css
border-radius: 6px;
overflow: hidden;
border: 1px solid var(--border);
background: var(--bg);
```

Active segment gets `--bg-elev` background and full opacity text. Inactive segments dim at 0.5 opacity.

---

## 11. Switches (iOS-style)

```css
width: 32px;
height: 18px;
border-radius: 9px;
```

Checked state: `--green` background with white thumb at `left: 16px`.
Unchecked: dim background with `--text-dim` thumb at `left: 2px`.
Transition: 0.2s cubic-bezier on both background and thumb position.

---

## 12. Window Controls (Backend)

Three API endpoints on the Flask backend control the pywebview window:

| Route | Method | Action |
|-------|--------|--------|
| `/api/window/close` | POST | `window.destroy()` (falls back to `os._exit(0)`) |
| `/api/window/minimize` | POST | `window.minimize()` |
| `/api/window/maximize` | POST | Toggles `window.maximize()` / `window.restore()` |

### 12.1 Window Dragging

The frameless titlebar is draggable via JS + Win32 API, not CSS `-webkit-app-region`:

**Frontend (app.js):**
- `@mousedown` on `.titlebar` starts drag tracking
- Skips drag if target is inside `.titlebar-actions`, `.titlebar-window-controls`, `.win-btn`, or `.titlebar-action-btn`
- On `mousemove`, calculates pixel delta and POSTs `{dx, dy}` to `/api/window/move`
- On `mouseup`, removes event listeners

**Backend (app.py):**
- `POST /api/window/move` receives `{dx, dy}`
- Uses `win32gui.GetWindowRect()` to get current position
- Uses `win32gui.SetWindowPos()` with `SWP_NOSIZE | SWP_NOZORDER` to move the window
- Finds the window HWND via `_webview_window.handle` or fallback `FindWindow` by title

The pywebview window reference is stored globally in `app._webview_window` and set by `launcher.py` after `webview.create_window()`.

---

## 13. Icons

All icons are inline SVGs with currentColor stroke. No icon font or emoji usage. Key icons:

| Component | SVG Description |
|-----------|-----------------|
| Settings | Gear with 4 spokes |
| Folder | Folder shape with tab |
| Refresh | Circular arrows |
| Close | Diagonal cross (X) |
| Minimize | Horizontal line |
| Maximize | Square outline |
| Activity | Three horizontal lines |
| Chevron | Triangle arrow (rotated for expand/collapse) |
| Send | Paper plane shape |
| Cancel | Filled rounded square (stop) |
| Tool call | Circle with crossed lines |
| Done | Rounded square (stop icon) |