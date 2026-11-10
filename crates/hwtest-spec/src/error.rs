//! Errors for YAML specifications, written for the people who edit them.
//!
//! Every problem carries the file, line/column and YAML key path, and all
//! problems found in a file are reported together:
//!
//! ```text
//! voltage_echo.yaml: 2 problems:
//!   voltage_echo.yaml:41:9: state_sequence[1]: unknown state "midle" (defined: minimum, middle, maximum; did you mean "middle"?)
//!   voltage_echo.yaml:63:26: monitors.echo.configuration.maximum.echo_voltage: within_range: expected [center, delta], got [4.0]
//! ```

use std::fmt::{self, Write as _};
use std::path::PathBuf;

/// One problem in a specification file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Issue {
    /// 1-based `(line, column)` if known.
    pub location: Option<(u64, u64)>,
    /// YAML key path, e.g. `monitors.echo.configuration.minimum.v`.
    pub path: String,
    /// What is wrong, in plain language.
    pub message: String,
}

impl Issue {
    /// Creates an issue.
    pub fn new(
        location: Option<(u64, u64)>,
        path: impl Into<String>,
        message: impl Into<String>,
    ) -> Self {
        Self { location, path: path.into(), message: message.into() }
    }

    fn fmt_with_file(&self, f: &mut fmt::Formatter<'_>, file: Option<&PathBuf>) -> fmt::Result {
        if let Some(file) = file {
            write!(f, "{}:", file.display())?;
        }
        if let Some((line, col)) = self.location {
            write!(f, "{line}:{col}:")?;
        }
        if file.is_some() || self.location.is_some() {
            f.write_str(" ")?;
        }
        if !self.path.is_empty() {
            write!(f, "{}: ", self.path)?;
        }
        f.write_str(&self.message)
    }
}

/// Error loading or validating a specification.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SpecError {
    /// A file could not be found.
    NotFound {
        /// What was being looked for.
        what: String,
        /// Locations that were searched.
        searched: Vec<PathBuf>,
        /// How to fix it.
        hint: String,
    },
    /// A file could not be read or written.
    Io {
        /// The file.
        path: PathBuf,
        /// OS error text.
        message: String,
    },
    /// The file was read but is not a valid specification.
    Invalid {
        /// The file, if loaded from disk.
        file: Option<PathBuf>,
        /// All problems found (never empty).
        issues: Vec<Issue>,
    },
}

impl SpecError {
    /// Creates an [`SpecError::Invalid`] with a single issue.
    pub fn invalid(file: Option<PathBuf>, issue: Issue) -> Self {
        SpecError::Invalid { file, issues: vec![issue] }
    }

    /// The issues, for [`SpecError::Invalid`].
    pub fn issues(&self) -> &[Issue] {
        match self {
            SpecError::Invalid { issues, .. } => issues,
            _ => &[],
        }
    }
}

impl fmt::Display for SpecError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SpecError::NotFound { what, searched, hint } => {
                write!(f, "{what} not found")?;
                if !searched.is_empty() {
                    let dirs: Vec<String> =
                        searched.iter().map(|p| p.display().to_string()).collect();
                    write!(f, " (searched: {})", dirs.join(", "))?;
                }
                if !hint.is_empty() {
                    write!(f, ". {hint}")?;
                }
                Ok(())
            }
            SpecError::Io { path, message } => write!(f, "{}: {message}", path.display()),
            SpecError::Invalid { file, issues } => {
                if let [issue] = issues.as_slice() {
                    return issue.fmt_with_file(f, file.as_ref());
                }
                let name =
                    file.as_ref().map_or("specification".to_string(), |p| p.display().to_string());
                write!(f, "{name}: {} problems:", issues.len())?;
                for issue in issues {
                    f.write_str("\n  ")?;
                    issue.fmt_with_file(f, file.as_ref())?;
                }
                Ok(())
            }
        }
    }
}

impl std::error::Error for SpecError {}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, SpecError>;

/// Accumulates issues while validating a file.
#[derive(Debug, Default)]
pub(crate) struct Issues(pub Vec<Issue>);

impl Issues {
    pub fn push(
        &mut self,
        location: Option<(u64, u64)>,
        path: impl Into<String>,
        msg: impl Into<String>,
    ) {
        self.0.push(Issue::new(location, path, msg));
    }

    pub fn finish<T>(self, file: Option<PathBuf>, value: T) -> Result<T> {
        if self.0.is_empty() {
            Ok(value)
        } else {
            Err(SpecError::Invalid { file, issues: self.0 })
        }
    }
}

/// Suggests the closest candidate for a misspelled name.
pub(crate) fn did_you_mean<'a>(
    name: &str,
    candidates: impl IntoIterator<Item = &'a str>,
) -> Option<&'a str> {
    candidates
        .into_iter()
        .map(|c| (levenshtein(name, c), c))
        .filter(|(d, c)| *d <= 2.max(c.len() / 4))
        .min_by_key(|(d, _)| *d)
        .map(|(_, c)| c)
}

fn levenshtein(a: &str, b: &str) -> usize {
    let b: Vec<char> = b.chars().collect();
    let mut prev: Vec<usize> = (0..=b.len()).collect();
    for (i, ca) in a.chars().enumerate() {
        let mut cur = vec![i + 1];
        for (j, cb) in b.iter().enumerate() {
            let sub = prev[j] + usize::from(ca != *cb);
            cur.push(sub.min(prev[j + 1] + 1).min(cur[j] + 1));
        }
        prev = cur;
    }
    prev[b.len()]
}

/// Formats "unknown X "name" (expected: a, b; did you mean "c"?)".
pub(crate) fn unknown(kind: &str, name: &str, candidates: &[&str]) -> String {
    let mut msg = format!("unknown {kind} {name:?}");
    if !candidates.is_empty() {
        let _ = write!(msg, " (expected one of: {}", candidates.join(", "));
        if let Some(s) = did_you_mean(name, candidates.iter().copied()) {
            let _ = write!(msg, "; did you mean {s:?}?");
        }
        msg.push(')');
    }
    msg
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suggestions() {
        assert_eq!(did_you_mean("midle", ["minimum", "middle", "maximum"]), Some("middle"));
        assert_eq!(
            did_you_mean("within_rnage", ["within_range", "less_than"]),
            Some("within_range")
        );
        assert_eq!(did_you_mean("xyz", ["minimum", "middle"]), None);
    }

    #[test]
    fn display_single_and_multiple() {
        let one =
            SpecError::invalid(Some("t.yaml".into()), Issue::new(Some((3, 5)), "a.b", "bad value"));
        assert_eq!(one.to_string(), "t.yaml:3:5: a.b: bad value");
        let many = SpecError::Invalid {
            file: Some("t.yaml".into()),
            issues: vec![Issue::new(Some((1, 1)), "x", "m1"), Issue::new(None, "", "m2")],
        };
        assert_eq!(many.to_string(), "t.yaml: 2 problems:\n  t.yaml:1:1: x: m1\n  t.yaml: m2");
    }
}
