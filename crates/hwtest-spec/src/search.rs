//! Locating specification files on disk.

use std::path::{Path, PathBuf};

use crate::error::{Result, SpecError};

/// Splits a `:`-separated path list (like `$PATH`), skipping empty entries.
pub fn split_path_list(list: &str) -> Vec<PathBuf> {
    list.split(':').filter(|p| !p.is_empty()).map(PathBuf::from).collect()
}

/// Search directories for test definitions: `.`, `./configs`, then
/// `$TEST_DEFINITION_PATH`.
pub fn definition_search_paths() -> Vec<PathBuf> {
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let mut paths = vec![cwd.clone(), cwd.join("configs")];
    if let Ok(env) = std::env::var("TEST_DEFINITION_PATH") {
        paths.extend(split_path_list(&env));
    }
    paths
}

/// Search directories for rack instance (calibration) files:
/// `$HWTEST_RACK_INSTANCE_PATH`, `~/.config/hwtest/racks`, `/etc/hwtest/racks`.
pub fn rack_instance_search_paths() -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if let Ok(env) = std::env::var("HWTEST_RACK_INSTANCE_PATH") {
        paths.extend(split_path_list(&env));
    }
    if let Some(home) = std::env::var_os("HOME") {
        paths.push(Path::new(&home).join(".config/hwtest/racks"));
    }
    paths.push(PathBuf::from("/etc/hwtest/racks"));
    paths
}

/// Returns the first `dir/name` that is a file, trying names in order within
/// each directory.
pub fn find_first(dirs: &[PathBuf], names: &[String]) -> Option<PathBuf> {
    dirs.iter()
        .filter(|d| d.is_dir())
        .find_map(|d| names.iter().map(|n| d.join(n)).find(|p| p.is_file()))
}

/// Like [`find_first`] but returns a descriptive [`SpecError::NotFound`].
///
/// # Errors
/// [`SpecError::NotFound`] listing the searched directories.
pub fn require_first(
    what: &str,
    dirs: &[PathBuf],
    names: &[String],
    hint: &str,
) -> Result<PathBuf> {
    find_first(dirs, names).ok_or_else(|| SpecError::NotFound {
        what: what.to_string(),
        searched: dirs.to_vec(),
        hint: hint.to_string(),
    })
}

/// File names tried for a YAML document called `stem`.
pub fn yaml_names(stem: &str, suffixes: &[&str]) -> Vec<String> {
    suffixes
        .iter()
        .flat_map(|suffix| ["yaml", "yml"].map(|ext| format!("{stem}{suffix}.{ext}")))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_in_order() {
        let a = tempfile::tempdir().unwrap();
        let b = tempfile::tempdir().unwrap();
        std::fs::write(b.path().join("t.yml"), "").unwrap();
        std::fs::write(b.path().join("t_definition.yaml"), "").unwrap();
        let dirs =
            vec![a.path().to_path_buf(), b.path().to_path_buf(), PathBuf::from("/nonexistent")];
        let names = yaml_names("t", &["", "_definition"]);
        assert_eq!(names, ["t.yaml", "t.yml", "t_definition.yaml", "t_definition.yml"]);
        assert_eq!(find_first(&dirs, &names), Some(b.path().join("t.yml")));
        let err = require_first("test definition 'x'", &dirs, &yaml_names("x", &[""]), "Set X")
            .unwrap_err();
        assert!(err.to_string().starts_with("test definition 'x' not found (searched: "));
        assert!(err.to_string().ends_with(". Set X"));
    }

    #[test]
    fn path_list() {
        assert_eq!(split_path_list("a::b:"), vec![PathBuf::from("a"), PathBuf::from("b")]);
    }
}
