//! TCP server exposing an emulator as a raw-socket SCPI instrument, so it can
//! be reached like a LAN PSU (`TCPIP::127.0.0.1::5025::SOCKET`).

use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};

use super::emulator::BkPsuEmulator;

/// Serves `emulator` on `listener` forever (one thread per client).
pub fn serve(listener: &TcpListener, emulator: &BkPsuEmulator) {
    for stream in listener.incoming().flatten() {
        let emu = emulator.clone();
        std::thread::spawn(move || handle(stream, &emu));
    }
}

fn handle(stream: TcpStream, emu: &BkPsuEmulator) {
    let peer = stream.peer_addr().map(|a| a.to_string()).unwrap_or_default();
    tracing::info!("client connected: {peer}");
    let Ok(reader_stream) = stream.try_clone() else { return };
    let mut writer = stream;
    for line in BufReader::new(reader_stream).lines() {
        let Ok(line) = line else { break };
        emu.handle_line(&line);
        if let Some(resp) = emu.take_response() {
            if writer.write_all(format!("{resp}\n").as_bytes()).is_err() {
                break;
            }
        }
    }
    tracing::info!("client disconnected: {peer}");
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bk::{BkPsu, BkPsuConfig};
    use crate::traits::{ChannelRef, PowerSupply};

    #[test]
    fn driver_over_tcp() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let emu = BkPsuEmulator::model_9130b("TCP1");
        let emu2 = emu.clone();
        std::thread::spawn(move || serve(&listener, &emu2));
        let cfg = BkPsuConfig {
            visa_address: format!("TCPIP::127.0.0.1::{port}::SOCKET"),
            channels: vec![],
            timeout: 2.0,
        };
        let mut psu = BkPsu::open(cfg).unwrap();
        psu.set_voltage(&ChannelRef::Id(1), 3.3).unwrap();
        assert_eq!(emu.channel_state(1).unwrap().0, 3.3);
    }
}
