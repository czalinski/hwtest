//! MCC DAQ HAT drivers against the emulated HAT stack.

use std::sync::Arc;
use std::time::Duration;

use hwtest_instruments::daqhats::emulator::{AnalogOut, EmulatedDaqHats};
use hwtest_instruments::daqhats::{ids, mcc118, mcc134, mcc152, tc_values, Backend};
use hwtest_instruments::{ChannelRef, Instrument};
use serde_json::json;

fn bench() -> (Arc<EmulatedDaqHats>, Backend) {
    let emu = Arc::new(EmulatedDaqHats::with_boards(&[
        (0, ids::MCC_152),
        (1, ids::MCC_134),
        (4, ids::MCC_118),
    ]));
    let backend: Backend = emu.clone();
    (emu, backend)
}

fn mcc152_cfg() -> mcc152::Config {
    serde_json::from_value(json!({
        "address": 0,
        "source_id": "dio",
        "dio_channels": [
            {"id": 0, "name": "relay", "direction": "OUTPUT", "initial_value": true},
            {"id": 2, "name": "estop", "direction": "INPUT"}
        ],
        "analog_channels": [{"id": 0, "name": "control_voltage", "initial_voltage": 1.5}]
    }))
    .unwrap()
}

#[test]
fn mcc152_directions_follow_libdaqhats_convention() {
    let (emu, hats) = bench();
    let mut dio = mcc152::Mcc152::open(hats, mcc152_cfg()).unwrap();
    // libdaqhats: 0 = output, 1 = input. INPUT lines must not be driven.
    assert_eq!(emu.digital_line(0, 2), Some((true, false)), "estop is an input");
    assert_eq!(emu.digital_line(0, 0), Some((false, true)), "relay is an output, initially on");
    assert_eq!(emu.analog_output(0, 0), Some(1.5));

    let d = dio.digital_io().unwrap();
    emu.set_digital_input(0, 2, true);
    assert!(d.read(&"estop".into()).unwrap());
    d.write(&"relay".into(), false).unwrap();
    assert_eq!(emu.digital_line(0, 0), Some((false, false)));
    let e = d.write(&"estop".into(), true).unwrap_err().to_string();
    assert!(e.contains("configured as an INPUT"), "{e}");

    let a = dio.analog_out().unwrap();
    a.write(&"control_voltage".into(), 4.0).unwrap();
    assert_eq!(a.last_written(&"control_voltage".into()).unwrap(), Some(4.0));
    assert!(a.write(&"control_voltage".into(), 5.5).is_err());
    assert_eq!(dio.identity().unwrap().serial, "EMU15200");
}

#[test]
fn mcc152_rejects_bad_config() {
    let (_, hats) = bench();
    let bad: mcc152::Config =
        serde_json::from_value(json!({"address": 0, "dio_channels": [{"id": 9, "name": "x"}]}))
            .unwrap();
    assert!(mcc152::Mcc152::open(hats.clone(), bad).err().unwrap().to_string().contains("0-7"));
    let dup: mcc152::Config = serde_json::from_value(json!({"address": 0, "dio_channels": [{"id": 0, "name": "x"}], "analog_channels": [{"id": 0, "name": "x"}]})).unwrap();
    assert!(mcc152::Mcc152::open(hats.clone(), dup)
        .err()
        .unwrap()
        .to_string()
        .contains("duplicate channel name"));
    let wrong_board: mcc152::Config = serde_json::from_value(json!({"address": 4})).unwrap();
    assert!(mcc152::Mcc152::open(hats, wrong_board)
        .err()
        .unwrap()
        .to_string()
        .contains("wrong HAT type"));
}

fn mcc118_cfg(rate: f64) -> mcc118::Config {
    serde_json::from_value(json!({"address": 4, "sample_rate": rate, "channels": [{"id": 0, "name": "echo"}, {"id": 3, "name": "ref"}]})).unwrap()
}

#[test]
fn mcc118_reads_wired_output_and_streams() {
    let (emu, hats) = bench();
    let mut dio = mcc152::Mcc152::open(hats.clone(), mcc152_cfg()).unwrap();
    emu.connect_analog(AnalogOut { address: 0, channel: 0 }, 4, 0);
    emu.set_analog_input(4, 3, 2.5);
    let mut daq = mcc118::Mcc118::open(hats, mcc118_cfg(1000.0), "voltage_daq").unwrap();

    dio.analog_out().unwrap().write(&"control_voltage".into(), 3.3).unwrap();
    assert_eq!(daq.analog_in().unwrap().read(&"echo".into()).unwrap(), 3.3);
    assert_eq!(daq.analog_in().unwrap().read(&ChannelRef::Id(3)).unwrap(), 2.5);

    let (tx, rx) = std::sync::mpsc::channel();
    let s = daq.stream().unwrap();
    assert_eq!(s.schema().source_id(), "voltage_daq");
    assert_eq!(
        s.schema().fields().iter().map(|f| f.name.as_str()).collect::<Vec<_>>(),
        ["echo", "ref"]
    );
    let rate = s
        .start_stream(Box::new(move |d| {
            let _ = tx.send(d);
        }))
        .unwrap();
    assert_eq!(rate, 1000.0);
    let first = rx.recv_timeout(Duration::from_secs(2)).unwrap();
    let second = rx.recv_timeout(Duration::from_secs(2)).unwrap();
    assert_eq!(first.samples.len(), 100, "batches of rate/10");
    assert_eq!(first.period_ns, 1_000_000);
    assert_eq!(
        second.timestamp_ns,
        first.timestamp_ns + 100 * first.period_ns,
        "contiguous timestamps"
    );
    assert_eq!(first.samples[0][0].as_f64(), 3.3);
    assert_eq!(first.samples[0][1].as_f64(), 2.5);
    assert!(daq
        .analog_in()
        .unwrap()
        .read(&"echo".into())
        .unwrap_err()
        .to_string()
        .contains("while streaming"));
    daq.stream().unwrap().stop_stream().unwrap();
    assert!(!daq.stream().unwrap().is_streaming());
    assert_eq!(
        daq.analog_in().unwrap().read(&"echo".into()).unwrap(),
        3.3,
        "single reads work again"
    );
}

#[test]
fn mcc118_scan_rate_is_limited_by_channel_count() {
    let (_, hats) = bench();
    assert_eq!(hats.mcc118_scan_actual_rate(8, 50_000.0).unwrap(), 12_500.0);
}

#[test]
fn mcc134_types_specials_and_stream() {
    let (emu, hats) = bench();
    let cfg: mcc134::Config = serde_json::from_value(json!({
        "address": 1, "update_interval": 0.05,
        "channels": [{"id": 0, "name": "tc_0", "tc_type": "TYPE_T"}, {"id": 1, "name": "tc_1"}]
    }))
    .unwrap();
    let mut tc = mcc134::Mcc134::open(hats, cfg, "thermo").unwrap();
    assert_eq!(emu.tc_type(1, 0), Some(2), "TYPE_T");
    assert_eq!(emu.tc_type(1, 1), Some(1), "TYPE_K default");
    emu.set_temperature(1, 0, 85.5);
    assert_eq!(tc.analog_in().unwrap().read(&"tc_0".into()).unwrap(), 85.5);
    emu.set_temperature(1, 1, tc_values::OPEN);
    let e = tc.analog_in().unwrap().read(&"tc_1".into()).unwrap_err().to_string();
    assert!(e.contains("open thermocouple"), "{e}");

    let (tx, rx) = std::sync::mpsc::channel();
    tc.stream()
        .unwrap()
        .start_stream(Box::new(move |d| {
            let _ = tx.send(d);
        }))
        .unwrap();
    let d = rx.recv_timeout(Duration::from_secs(2)).unwrap();
    tc.stream().unwrap().stop_stream().unwrap();
    assert_eq!(d.samples[0][0].as_f64(), 85.5);
    assert!(d.samples[0][1].as_f64().is_nan(), "open TC streams as NaN");
}
