//! CAN bus access: Linux `SocketCAN` (`can0`, `vcan0`) or an in-process
//! virtual bus (`virtual:<name>`) for tests.
//!
//! On Linux a virtual `SocketCAN` interface lets the rack and the UUT
//! simulator talk without hardware:
//! `sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up`.

use std::collections::HashMap;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use socketcan::{CanFrame, CanSocket, EmbeddedFrame, ExtendedId, Id, Socket, StandardId};

use crate::error::{CommsExt, InstrumentError, Result};

/// A CAN frame.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanMessage {
    /// Arbitration id (11-bit, or 29-bit when `is_extended_id`).
    pub arbitration_id: u32,
    /// Payload (0-8 bytes for classic CAN).
    #[serde(default)]
    pub data: Vec<u8>,
    /// 29-bit id.
    #[serde(default)]
    pub is_extended_id: bool,
    /// CAN FD frame (not sent by the classic socket).
    #[serde(default)]
    pub is_fd: bool,
    /// Receive time, ns since the epoch (0 for frames to send).
    #[serde(default)]
    pub timestamp_ns: u64,
}

impl CanMessage {
    /// A standard-id frame.
    pub fn new(arbitration_id: u32, data: &[u8]) -> Self {
        Self {
            arbitration_id,
            data: data.to_vec(),
            is_extended_id: arbitration_id > 0x7FF,
            is_fd: false,
            timestamp_ns: 0,
        }
    }

    /// Checks id range and payload length.
    ///
    /// # Errors
    /// [`InstrumentError::OutOfRange`].
    pub fn validate(&self) -> Result<()> {
        let max_id = if self.is_extended_id { 0x1FFF_FFFF } else { 0x7FF };
        if self.arbitration_id > max_id {
            return Err(InstrumentError::OutOfRange(format!(
                "CAN id {:#x} exceeds {max_id:#x}",
                self.arbitration_id
            )));
        }
        let max_len = if self.is_fd { 64 } else { 8 };
        if self.data.len() > max_len {
            return Err(InstrumentError::OutOfRange(format!(
                "CAN payload of {} bytes exceeds {max_len}",
                self.data.len()
            )));
        }
        Ok(())
    }
}

fn now_ns() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| u64::try_from(d.as_nanos()).unwrap_or(u64::MAX))
}

/// A CAN interface. `send` and `recv` may be called from different threads.
pub trait CanBus: Send + Sync {
    /// Interface name.
    fn name(&self) -> &str;
    /// Sends a frame.
    ///
    /// # Errors
    /// Invalid frame or bus error.
    fn send(&self, msg: &CanMessage) -> Result<()>;
    /// Receives the next frame, or `None` after `timeout`.
    ///
    /// # Errors
    /// Bus error.
    fn recv(&self, timeout: Duration) -> Result<Option<CanMessage>>;
}

/// Linux `SocketCAN`.
pub struct SocketCanBus {
    name: String,
    socket: CanSocket,
}

impl SocketCanBus {
    /// Opens `interface` (e.g. `can0`).
    ///
    /// # Errors
    /// [`InstrumentError::Comms`] if the interface does not exist or is down.
    pub fn open(interface: &str) -> Result<Self> {
        let socket = CanSocket::open(interface).comms(format!(
            "CAN {interface} (is it up? sudo ip link set {interface} up type can bitrate 500000)"
        ))?;
        Ok(Self { name: interface.to_string(), socket })
    }
}

impl CanBus for SocketCanBus {
    fn name(&self) -> &str {
        &self.name
    }

    fn send(&self, msg: &CanMessage) -> Result<()> {
        msg.validate()?;
        if msg.is_fd {
            return Err(InstrumentError::Unsupported {
                instrument: self.name.clone(),
                capability: "CAN FD frames",
            });
        }
        let id: Id = if msg.is_extended_id {
            ExtendedId::new(msg.arbitration_id).map(Id::Extended)
        } else {
            u16::try_from(msg.arbitration_id).ok().and_then(StandardId::new).map(Id::Standard)
        }
        .ok_or_else(|| {
            InstrumentError::OutOfRange(format!("bad CAN id {:#x}", msg.arbitration_id))
        })?;
        let frame = CanFrame::new(id, &msg.data)
            .ok_or_else(|| InstrumentError::OutOfRange("bad CAN frame".into()))?;
        self.socket.write_frame(&frame).comms(&self.name)
    }

    fn recv(&self, timeout: Duration) -> Result<Option<CanMessage>> {
        match self.socket.read_frame_timeout(timeout) {
            Ok(frame) => {
                let (arbitration_id, is_extended_id) = match frame.id() {
                    Id::Standard(s) => (u32::from(s.as_raw()), false),
                    Id::Extended(e) => (e.as_raw(), true),
                };
                Ok(Some(CanMessage {
                    arbitration_id,
                    data: frame.data().to_vec(),
                    is_extended_id,
                    is_fd: false,
                    timestamp_ns: now_ns(),
                }))
            }
            Err(e)
                if matches!(
                    e.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) =>
            {
                Ok(None)
            }
            Err(e) => Err(InstrumentError::Comms(format!("{}: {e}", self.name))),
        }
    }
}

/// A port on an in-process virtual bus: receives every frame sent by other
/// ports on the same bus (like a real bus, not its own frames).
pub struct VirtualCanPort {
    name: String,
    id: usize,
    net: Arc<VirtualNet>,
    rx: Mutex<Receiver<CanMessage>>,
}

#[derive(Default)]
struct VirtualNet {
    ports: Mutex<Vec<(usize, Sender<CanMessage>)>>,
    next: Mutex<usize>,
}

fn networks() -> &'static Mutex<HashMap<String, Arc<VirtualNet>>> {
    static NETS: OnceLock<Mutex<HashMap<String, Arc<VirtualNet>>>> = OnceLock::new();
    NETS.get_or_init(Mutex::default)
}

impl VirtualCanPort {
    /// Connects a new port to the virtual bus `name`.
    pub fn connect(name: &str) -> Self {
        let net = networks()
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .entry(name.to_string())
            .or_default()
            .clone();
        let (tx, rx) = mpsc::channel();
        let id = {
            let mut n = net.next.lock().unwrap_or_else(PoisonError::into_inner);
            *n += 1;
            *n
        };
        net.ports.lock().unwrap_or_else(PoisonError::into_inner).push((id, tx));
        Self { name: format!("virtual:{name}"), id, net, rx: Mutex::new(rx) }
    }
}

impl CanBus for VirtualCanPort {
    fn name(&self) -> &str {
        &self.name
    }

    fn send(&self, msg: &CanMessage) -> Result<()> {
        msg.validate()?;
        let mut m = msg.clone();
        m.timestamp_ns = now_ns();
        self.net
            .ports
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .retain(|(id, tx)| *id == self.id || tx.send(m.clone()).is_ok());
        Ok(())
    }

    fn recv(&self, timeout: Duration) -> Result<Option<CanMessage>> {
        match self.rx.lock().unwrap_or_else(PoisonError::into_inner).recv_timeout(timeout) {
            Ok(m) => Ok(Some(m)),
            Err(RecvTimeoutError::Timeout | RecvTimeoutError::Disconnected) => Ok(None),
        }
    }
}

impl Drop for VirtualCanPort {
    fn drop(&mut self) {
        self.net
            .ports
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .retain(|(id, _)| *id != self.id);
    }
}

/// Opens `virtual:<name>` or a `SocketCAN` interface.
///
/// # Errors
/// As [`SocketCanBus::open`].
pub fn open_can(interface: &str) -> Result<Arc<dyn CanBus>> {
    match interface.strip_prefix("virtual:") {
        Some(name) => Ok(Arc::new(VirtualCanPort::connect(name))),
        None => Ok(Arc::new(SocketCanBus::open(interface)?)),
    }
}

/// Waits for a frame with `id`, discarding others; `None` after `timeout`.
///
/// # Errors
/// Bus error.
pub fn wait_for(bus: &dyn CanBus, id: u32, timeout: Duration) -> Result<Option<CanMessage>> {
    let deadline = std::time::Instant::now() + timeout;
    loop {
        let left = deadline.saturating_duration_since(std::time::Instant::now());
        if left.is_zero() {
            return Ok(None);
        }
        if let Some(m) = bus.recv(left.min(Duration::from_millis(50)))? {
            if m.arbitration_id == id {
                return Ok(Some(m));
            }
        }
    }
}

/// Collects frames for `duration`, optionally only those with `id`.
///
/// # Errors
/// Bus error.
pub fn collect(bus: &dyn CanBus, duration: Duration, id: Option<u32>) -> Result<Vec<CanMessage>> {
    let deadline = std::time::Instant::now() + duration;
    let mut out = Vec::new();
    loop {
        let left = deadline.saturating_duration_since(std::time::Instant::now());
        if left.is_zero() {
            return Ok(out);
        }
        if let Some(m) = bus.recv(left.min(Duration::from_millis(50)))? {
            if id.is_none_or(|i| i == m.arbitration_id) {
                out.push(m);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn virtual_bus_delivers_to_other_ports() {
        let a = open_can("virtual:t1").unwrap();
        let b = open_can("virtual:t1").unwrap();
        let other = open_can("virtual:t2").unwrap();
        a.send(&CanMessage::new(0x200, &[1, 2, 3])).unwrap();
        let m = b.recv(Duration::from_secs(1)).unwrap().unwrap();
        assert_eq!(
            (m.arbitration_id, m.data.as_slice(), m.is_extended_id),
            (0x200, &[1u8, 2, 3][..], false)
        );
        assert!(m.timestamp_ns > 0);
        assert!(a.recv(Duration::from_millis(20)).unwrap().is_none(), "no self-reception");
        assert!(other.recv(Duration::from_millis(20)).unwrap().is_none(), "separate bus");
        b.send(&CanMessage::new(0x100, &[9])).unwrap();
        b.send(&CanMessage::new(0x210, &[7])).unwrap();
        assert_eq!(
            wait_for(a.as_ref(), 0x210, Duration::from_secs(1)).unwrap().unwrap().data,
            vec![7]
        );
    }

    #[test]
    fn validation() {
        assert!(CanMessage::new(0x800, &[]).is_extended_id);
        let mut m = CanMessage::new(0x100, &[0; 9]);
        assert!(m.validate().unwrap_err().to_string().contains("exceeds 8"));
        m.data.truncate(8);
        m.is_extended_id = false;
        m.arbitration_id = 0x900;
        assert!(m.validate().is_err());
        assert!(open_can("hwtest_no_such_can").err().unwrap().to_string().contains("is it up"));
    }
}
