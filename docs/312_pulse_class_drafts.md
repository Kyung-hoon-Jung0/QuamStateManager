# 312 Pulse class drafts: the server rules of the edit screen

The pulse-class edit screen (`/pulse-class`) keeps one draft file per class in the instance folder
(`core/pulse_class_drafts.py`); the code pane and any outside editor both write it, and
`core/pulse_class_watch.py` keeps them apart.

## Rules

- **Settle rule.** An outside change is read only after the file's size and mtime are equal across two looks,
  and the read length matches the stat. SM never writes the draft because of an outside change.
- **Two writers.** A pane save whose base differs from the file is refused with one question naming what each
  choice loses. The question carries `file_sha` (the file content it is about); the answer (`keep` = `sm` or
  `file`) sends it back, and a file that moved since is asked about again. `keep="file"` reads by the settle
  rule and refuses while the file is still being written.
- **Template region.** A pane save may not change the template region (the two markers and the lines between
  them) unless the user unlocked those lines (`hand_edit: true`); the refusal names the field, the formula or
  the markers. The form door rewrites only that region.
- **Save to file.** The class file is an absolute `.py` path in an existing folder; a relative path, a folder,
  a Windows device name (`CON`, `NUL`, `COM1`, ...) is refused with the reason. The draft must parse and must
  have previewed successfully in the lab env in exactly these bytes. A file already there with other bytes is
  replaced only after the user confirmed that exact content (`replace_sha`, a 409 `confirm` first) or when it
  is SM's own last save there; the replaced file is copied next to it as
  `<name>.py.sm-backup-YYYYMMDD-HHMMSS.bak`.
- **Deleted draft.** The status line says `The draft file is gone: <path>`.

## Known limits

- An outside edit that keeps the same size and the same mtime (a tool that restores the mtime) is never seen.
- A writer that pauses for more than about 1 s in the middle of a write (one poll; 0.25 s while a "Keep the
  file" answer is read) can have its half file read as settled; the parse check on Save to file refuses such a draft if it does not parse.
