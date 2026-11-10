//! YAML loading helpers (YAML 1.2 via `serde-saphyr`).

use std::path::{Path, PathBuf};

use serde::de::DeserializeOwned;
use serde_saphyr::Spanned;

use crate::error::{Issue, Result, SpecError};

/// 1-based `(line, column)` of a spanned node.
pub(crate) fn loc<T>(s: &Spanned<T>) -> Option<(u64, u64)> {
    let l = s.referenced;
    (l.line() > 0).then(|| (l.line(), l.column()))
}

/// Reads a file to a string.
pub(crate) fn read_file(path: &Path) -> Result<String> {
    std::fs::read_to_string(path).map_err(|e| {
        if e.kind() == std::io::ErrorKind::NotFound {
            SpecError::NotFound {
                what: path.display().to_string(),
                searched: vec![],
                hint: String::new(),
            }
        } else {
            SpecError::Io { path: path.to_path_buf(), message: e.to_string() }
        }
    })
}

/// Parses YAML text into `T`, turning syntax/type errors into a located issue.
pub(crate) fn parse<T: DeserializeOwned>(text: &str, file: Option<&PathBuf>) -> Result<T> {
    serde_saphyr::from_str::<T>(text).map_err(|e| {
        let location = e.location().filter(|l| l.line() > 0).map(|l| (l.line(), l.column()));
        let message = e.without_snippet().to_string();
        // saphyr prefixes "line:col:" itself in some messages; keep ours canonical.
        let message = strip_location_suffix(strip_location_prefix(&message).trim()).to_string();
        SpecError::invalid(file.cloned(), Issue::new(location, "", message))
    })
}

/// Removes a trailing " at line N, column M" (the location is reported separately).
fn strip_location_suffix(msg: &str) -> &str {
    match msg.rfind(" at line ") {
        Some(i) if msg[i..].contains(", column ") => &msg[..i],
        _ => msg,
    }
}

fn strip_location_prefix(msg: &str) -> &str {
    // e.g. "error: line 3 column 5: ..." -> "..."
    let m = msg.strip_prefix("error: ").unwrap_or(msg);
    if let Some(rest) = m.strip_prefix("line ") {
        if let Some(idx) = rest.find(": ") {
            if rest[..idx].contains("column") {
                return &rest[idx + 2..];
            }
        }
    }
    m
}

/// Converts a JSON value to JSON text, which is valid YAML 1.2, so values that
/// did not come from a file (e.g. Python dicts) share one parsing path.
pub(crate) fn json_as_yaml(value: &serde_json::Value) -> String {
    value.to_string()
}

/// Removes line/column positions, which are meaningless for values that were
/// not read from a file (they would point into generated JSON text).
pub(crate) fn without_locations<T>(result: Result<T>) -> Result<T> {
    result.map_err(|e| match e {
        SpecError::Invalid { file, issues } => SpecError::Invalid {
            file,
            issues: issues.into_iter().map(|i| Issue { location: None, ..i }).collect(),
        },
        other => other,
    })
}
