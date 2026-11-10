//! SCPI transports: raw TCP socket, serial port, Linux USBTMC, emulators.
//!
//! Address formats (VISA-style strings are accepted for compatibility):
//!
//! | Address | Transport |
//! |---------|-----------|
//! | `TCPIP::192.168.1.50::5025::SOCKET`, `tcp://192.168.1.50:5025` | raw socket (default port 5025) |
//! | `ASRL/dev/ttyUSB0::INSTR`, `serial:///dev/ttyUSB0?baud=9600` | serial (default 9600 8N1) |
//! | `/dev/usbtmc0`, `usbtmc:///dev/usbtmc0` | Linux USBTMC character device |
//! | `emulator:bk9115`, `emulator:bk9130b` | in-process BK Precision emulator |

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::time::Duration;

use crate::error::{CommsExt, InstrumentError, Result};

/// Line-oriented SCPI transport (newline-terminated messages).
pub trait ScpiTransport: Send {
    /// Sends one message (terminator added).
    ///
    /// # Errors
    /// I/O failure.
    fn write(&mut self, message: &str) -> Result<()>;
    /// Reads one response (terminator removed).
    ///
    /// # Errors
    /// I/O failure or timeout.
    fn read(&mut self) -> Result<String>;
    /// Closes the connection.
    ///
    /// # Errors
    /// I/O failure.
    fn close(&mut self) -> Result<()> {
        Ok(())
    }
}

/// Generic newline transport over any reader/writer pair.
pub struct StreamTransport<R: Read + Send, W: Write + Send> {
    name: String,
    reader: BufReader<R>,
    writer: W,
}

impl<R: Read + Send, W: Write + Send> StreamTransport<R, W> {
    fn new(name: String, reader: R, writer: W) -> Self {
        Self { name, reader: BufReader::new(reader), writer }
    }
}

impl<R: Read + Send, W: Write + Send> ScpiTransport for StreamTransport<R, W> {
    fn write(&mut self, message: &str) -> Result<()> {
        self.writer.write_all(message.as_bytes()).comms(&self.name)?;
        self.writer.write_all(b"\n").comms(&self.name)?;
        self.writer.flush().comms(&self.name)
    }

    fn read(&mut self) -> Result<String> {
        let mut line = String::new();
        let n = self.reader.read_line(&mut line).comms(&self.name)?;
        if n == 0 {
            return Err(InstrumentError::Comms(format!("{}: connection closed", self.name)));
        }
        Ok(line.trim_end_matches(['\r', '\n']).to_string())
    }
}

/// Opens a transport from an address string.
///
/// # Errors
/// [`InstrumentError::Config`] for unrecognised addresses, or
/// [`InstrumentError::Comms`] if the device cannot be opened.
pub fn open_transport(address: &str, timeout: Duration) -> Result<Box<dyn ScpiTransport>> {
    let a = address.trim();
    let upper = a.to_ascii_uppercase();
    if let Some(model) = a.strip_prefix("emulator:") {
        return crate::bk::emulator::BkPsuEmulator::by_name(model)
            .map(|e| Box::new(e) as Box<dyn ScpiTransport>);
    }
    if upper.starts_with("TCPIP") || a.starts_with("tcp://") {
        let (host, port) = parse_tcp(a)?;
        let addr = format!("{host}:{port}");
        let sock = std::net::ToSocketAddrs::to_socket_addrs(&addr)
            .comms(&addr)?
            .next()
            .ok_or_else(|| InstrumentError::Comms(format!("{addr}: no address")))?;
        let stream = TcpStream::connect_timeout(&sock, timeout).comms(&addr)?;
        stream.set_read_timeout(Some(timeout)).comms(&addr)?;
        stream.set_nodelay(true).comms(&addr)?;
        let reader = stream.try_clone().comms(&addr)?;
        return Ok(Box::new(StreamTransport::new(addr, reader, stream)));
    }
    if upper.starts_with("ASRL") || a.starts_with("serial://") {
        let (path, baud) = parse_serial(a)?;
        let port = serialport::new(&path, baud).timeout(timeout).open().comms(&path)?;
        let reader = port.try_clone().comms(&path)?;
        return Ok(Box::new(StreamTransport::new(path, reader, port)));
    }
    let usbtmc = a.strip_prefix("usbtmc://").unwrap_or(a);
    if usbtmc.starts_with("/dev/usbtmc") {
        let file = std::fs::OpenOptions::new().read(true).write(true).open(usbtmc).comms(usbtmc)?;
        let reader = file.try_clone().comms(usbtmc)?;
        return Ok(Box::new(StreamTransport::new(usbtmc.to_string(), reader, file)));
    }
    Err(InstrumentError::Config(format!(
        "unrecognised instrument address {address:?} (use TCPIP::host::5025::SOCKET, ASRL/dev/ttyUSB0::INSTR, /dev/usbtmc0 or emulator:<model>)"
    )))
}

fn parse_tcp(a: &str) -> Result<(String, u16)> {
    if let Some(rest) = a.strip_prefix("tcp://") {
        let (host, port) = rest.rsplit_once(':').map_or((rest, "5025"), |(h, p)| (h, p));
        let port =
            port.parse().map_err(|_| InstrumentError::Config(format!("bad port in {a:?}")))?;
        return Ok((host.to_string(), port));
    }
    // TCPIP[board]::host[::port][::SOCKET|::INSTR]
    let parts: Vec<&str> = a.split("::").collect();
    let host =
        parts.get(1).ok_or_else(|| InstrumentError::Config(format!("missing host in {a:?}")))?;
    let port = parts.get(2).and_then(|p| p.parse().ok()).unwrap_or(5025);
    Ok(((*host).to_string(), port))
}

fn parse_serial(a: &str) -> Result<(String, u32)> {
    if let Some(rest) = a.strip_prefix("serial://") {
        let (path, query) = rest.split_once('?').unwrap_or((rest, ""));
        let baud = query
            .split('&')
            .find_map(|kv| kv.strip_prefix("baud="))
            .map_or(Ok(9600), str::parse)
            .map_err(|_| InstrumentError::Config(format!("bad baud rate in {a:?}")))?;
        return Ok((path.to_string(), baud));
    }
    // ASRL/dev/ttyUSB0::INSTR
    let body = &a[4..];
    let path = body.split("::").next().unwrap_or(body);
    Ok((path.to_string(), 9600))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn address_parsing() {
        assert_eq!(parse_tcp("TCPIP0::10.0.0.5::5025::SOCKET").unwrap(), ("10.0.0.5".into(), 5025));
        assert_eq!(parse_tcp("TCPIP::psu.local::INSTR").unwrap(), ("psu.local".into(), 5025));
        assert_eq!(parse_tcp("tcp://h:6000").unwrap(), ("h".into(), 6000));
        assert_eq!(parse_serial("ASRL/dev/ttyUSB0::INSTR").unwrap(), ("/dev/ttyUSB0".into(), 9600));
        assert_eq!(
            parse_serial("serial:///dev/ttyS1?baud=57600").unwrap(),
            ("/dev/ttyS1".into(), 57600)
        );
        assert!(open_transport("GPIB0::5::INSTR", Duration::from_millis(10)).is_err());
    }

    #[test]
    fn tcp_round_trip() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            let (s, _) = listener.accept().unwrap();
            let mut r = BufReader::new(s.try_clone().unwrap());
            let mut w = s;
            let mut line = String::new();
            r.read_line(&mut line).unwrap();
            assert_eq!(line, "*IDN?\n");
            w.write_all(b"ACME,X1,42,1.0\r\n").unwrap();
        });
        let mut t =
            open_transport(&format!("TCPIP::127.0.0.1::{port}::SOCKET"), Duration::from_secs(2))
                .unwrap();
        t.write("*IDN?").unwrap();
        assert_eq!(t.read().unwrap(), "ACME,X1,42,1.0");
    }
}
