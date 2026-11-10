//! SCPI: number parsing, `*IDN?`, error-queue checking, transports.

pub mod transport;

use hwtest_core::InstrumentIdentity;

use crate::error::{InstrumentError, Result};
pub use transport::{open_transport, ScpiTransport};

/// Parses a SCPI number, including `NAN`, `INF`, `NINF`/`-INF`.
///
/// # Errors
/// [`InstrumentError::Comms`] for unparsable text.
pub fn parse_number(text: &str) -> Result<f64> {
    let t = text.trim().to_ascii_uppercase();
    match t.as_str() {
        "NAN" => Ok(f64::NAN),
        "INF" | "+INF" => Ok(f64::INFINITY),
        "NINF" | "-INF" => Ok(f64::NEG_INFINITY),
        _ => {
            t.parse().map_err(|_| InstrumentError::Comms(format!("invalid SCPI number: {text:?}")))
        }
    }
}

/// Parses comma-separated numbers.
///
/// # Errors
/// [`InstrumentError::Comms`] if any element is invalid.
pub fn parse_numbers(text: &str) -> Result<Vec<f64>> {
    text.split(',').map(parse_number).collect()
}

/// Parses a SCPI boolean (`1`/`0`/`ON`/`OFF`).
///
/// # Errors
/// [`InstrumentError::Comms`] for other text.
pub fn parse_bool(text: &str) -> Result<bool> {
    match text.trim().to_ascii_uppercase().as_str() {
        "1" | "ON" => Ok(true),
        "0" | "OFF" => Ok(false),
        _ => Err(InstrumentError::Comms(format!("invalid SCPI boolean: {text:?}"))),
    }
}

/// Parses a `*IDN?` response (`manufacturer,model,serial,firmware...`).
///
/// # Errors
/// [`InstrumentError::Comms`] if fewer than four fields.
pub fn parse_idn(response: &str) -> Result<InstrumentIdentity> {
    let parts: Vec<&str> = response.split(',').map(str::trim).collect();
    if parts.len() < 4 {
        return Err(InstrumentError::Comms(format!(
            "expected 4 fields in *IDN? response, got {}: {response:?}",
            parts.len()
        )));
    }
    Ok(InstrumentIdentity {
        manufacturer: parts[0].into(),
        model: parts[1].into(),
        serial: parts[2].into(),
        firmware: parts[3..].join(","),
    })
}

/// One entry of the instrument's error queue.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScpiErrorEntry {
    /// Error code (non-zero).
    pub code: i32,
    /// Message.
    pub message: String,
}

impl std::fmt::Display for ScpiErrorEntry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{},\"{}\"", self.code, self.message)
    }
}

/// Parses a `SYST:ERR?` response; `None` for code 0 or unparsable text.
pub fn parse_error_entry(raw: &str) -> Option<ScpiErrorEntry> {
    let (code, msg) = raw.trim().split_once(',')?;
    let code: i32 = code.trim().parse().ok()?;
    (code != 0)
        .then(|| ScpiErrorEntry { code, message: msg.trim().trim_matches('"').trim().to_string() })
}

/// Command/query connection with automatic error-queue checking.
pub struct ScpiConnection {
    transport: Box<dyn ScpiTransport>,
    check_errors: bool,
}

impl ScpiConnection {
    /// Wraps a transport. With `check_errors`, every command/query is followed
    /// by draining `SYST:ERR?` and failing if the queue held errors.
    pub fn new(transport: Box<dyn ScpiTransport>, check_errors: bool) -> Self {
        Self { transport, check_errors }
    }

    /// Sends a command.
    ///
    /// # Errors
    /// Transport failure or instrument errors.
    pub fn command(&mut self, cmd: &str) -> Result<()> {
        self.transport.write(cmd)?;
        self.check()
    }

    /// Sends a query and returns the trimmed response.
    ///
    /// # Errors
    /// Transport failure or instrument errors.
    pub fn query(&mut self, cmd: &str) -> Result<String> {
        self.transport.write(cmd)?;
        let r = self.transport.read()?.trim().to_string();
        self.check()?;
        Ok(r)
    }

    /// Query returning a number.
    ///
    /// # Errors
    /// As [`ScpiConnection::query`], or an unparsable response.
    pub fn query_number(&mut self, cmd: &str) -> Result<f64> {
        parse_number(&self.query(cmd)?)
    }

    /// Query returning numbers.
    ///
    /// # Errors
    /// As [`ScpiConnection::query`], or an unparsable response.
    pub fn query_numbers(&mut self, cmd: &str) -> Result<Vec<f64>> {
        parse_numbers(&self.query(cmd)?)
    }

    /// Query returning an integer.
    ///
    /// # Errors
    /// As [`ScpiConnection::query`], or an unparsable response.
    pub fn query_int(&mut self, cmd: &str) -> Result<i64> {
        let r = self.query(cmd)?;
        r.trim().parse().map_err(|_| InstrumentError::Comms(format!("invalid SCPI integer: {r:?}")))
    }

    /// Query returning a boolean.
    ///
    /// # Errors
    /// As [`ScpiConnection::query`], or an unparsable response.
    pub fn query_bool(&mut self, cmd: &str) -> Result<bool> {
        parse_bool(&self.query(cmd)?)
    }

    /// `*IDN?` parsed.
    ///
    /// # Errors
    /// As [`ScpiConnection::query`].
    pub fn identity(&mut self) -> Result<InstrumentIdentity> {
        parse_idn(&self.query("*IDN?")?)
    }

    /// Drains the error queue.
    ///
    /// # Errors
    /// Transport failure.
    pub fn errors(&mut self) -> Result<Vec<ScpiErrorEntry>> {
        let mut out = Vec::new();
        // Bounded so a misbehaving instrument cannot loop forever.
        for _ in 0..64 {
            self.transport.write("SYST:ERR?")?;
            match parse_error_entry(&self.transport.read()?) {
                Some(e) => out.push(e),
                None => break,
            }
        }
        Ok(out)
    }

    fn check(&mut self) -> Result<()> {
        if !self.check_errors {
            return Ok(());
        }
        let errors = self.errors()?;
        if errors.is_empty() {
            Ok(())
        } else {
            Err(InstrumentError::Instrument(
                errors.iter().map(ToString::to_string).collect::<Vec<_>>().join("; "),
            ))
        }
    }

    /// Closes the transport.
    ///
    /// # Errors
    /// Transport failure.
    pub fn close(&mut self) -> Result<()> {
        self.transport.close()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbers_and_bools() {
        assert_eq!(parse_number(" 1.5E+01 ").unwrap(), 15.0);
        assert!(parse_number("nan").unwrap().is_nan());
        assert_eq!(parse_number("-INF").unwrap(), f64::NEG_INFINITY);
        assert!(parse_number("abc").is_err());
        assert_eq!(parse_numbers("1,2.5").unwrap(), vec![1.0, 2.5]);
        assert!(parse_bool("ON").unwrap() && !parse_bool("0").unwrap());
        assert!(parse_bool("maybe").is_err());
    }

    #[test]
    fn idn_and_errors() {
        let id = parse_idn("B&K Precision, 9130B, SN1, V1.00, extra").unwrap();
        assert_eq!((id.model.as_str(), id.firmware.as_str()), ("9130B", "V1.00,extra"));
        assert!(parse_idn("a,b").is_err());
        assert_eq!(
            parse_error_entry("-100,\"Command error\""),
            Some(ScpiErrorEntry { code: -100, message: "Command error".into() })
        );
        assert_eq!(parse_error_entry("0,\"No error\""), None);
        assert_eq!(parse_error_entry("garbage"), None);
    }
}
