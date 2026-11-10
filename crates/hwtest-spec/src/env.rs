//! `${VAR}` / `${VAR:default}` substitution in configuration values.

use serde_json::Value;

/// Replaces `${VAR}` and `${VAR:default}` in every string inside `value`.
///
/// `lookup` returns the variable's value, if set. A reference to an unset
/// variable without a default is an error naming the variable.
///
/// # Errors
/// Returns the names of unset variables that have no default.
pub fn substitute(
    value: &Value,
    lookup: &dyn Fn(&str) -> Option<String>,
) -> Result<Value, Vec<String>> {
    let mut missing = Vec::new();
    let out = walk(value, lookup, &mut missing);
    if missing.is_empty() {
        Ok(out)
    } else {
        missing.dedup();
        Err(missing)
    }
}

/// [`substitute`] using the process environment.
///
/// # Errors
/// Returns the names of unset variables that have no default.
pub fn substitute_env(value: &Value) -> Result<Value, Vec<String>> {
    substitute(value, &|name| std::env::var(name).ok())
}

fn walk(
    value: &Value,
    lookup: &dyn Fn(&str) -> Option<String>,
    missing: &mut Vec<String>,
) -> Value {
    match value {
        Value::String(s) => Value::String(expand(s, lookup, missing)),
        Value::Array(items) => {
            Value::Array(items.iter().map(|v| walk(v, lookup, missing)).collect())
        }
        Value::Object(map) => {
            Value::Object(map.iter().map(|(k, v)| (k.clone(), walk(v, lookup, missing))).collect())
        }
        other => other.clone(),
    }
}

fn expand(s: &str, lookup: &dyn Fn(&str) -> Option<String>, missing: &mut Vec<String>) -> String {
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(start) = rest.find("${") {
        out.push_str(&rest[..start]);
        let after = &rest[start + 2..];
        let Some(end) = after.find('}') else {
            out.push_str(&rest[start..]);
            return out;
        };
        let expr = &after[..end];
        let (name, default) = match expr.split_once(':') {
            Some((n, d)) => (n, Some(d)),
            None => (expr, None),
        };
        match (lookup(name), default) {
            (Some(v), _) => out.push_str(&v),
            (None, Some(d)) => out.push_str(d),
            (None, None) => missing.push(name.to_string()),
        }
        rest = &after[end + 1..];
    }
    out.push_str(rest);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn env(name: &str) -> Option<String> {
        (name == "HOST").then(|| "influx.local".to_string())
    }

    #[test]
    fn substitutes_nested_values() {
        let v = json!({"url": "http://${HOST}:${PORT:8086}/x", "list": ["${HOST}"], "n": 3});
        assert_eq!(
            substitute(&v, &env).unwrap(),
            json!({"url": "http://influx.local:8086/x", "list": ["influx.local"], "n": 3})
        );
    }

    #[test]
    fn default_may_contain_colons_and_be_empty() {
        let v = json!(["${URL:http://localhost:8086}", "${TOKEN:}"]);
        assert_eq!(substitute(&v, &env).unwrap(), json!(["http://localhost:8086", ""]));
    }

    #[test]
    fn reports_missing_variables() {
        let v = json!({"token": "${INFLUXDB_TOKEN}", "x": "${A}${A}"});
        assert_eq!(substitute(&v, &env).unwrap_err(), vec!["INFLUXDB_TOKEN", "A"]);
    }

    #[test]
    fn unterminated_reference_is_left_alone() {
        assert_eq!(substitute(&json!("${OOPS"), &env).unwrap(), json!("${OOPS"));
    }
}
