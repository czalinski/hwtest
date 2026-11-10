//! YAML specifications for hwtest.
//!
//! These files are the interface to the hardware engineers and designers who
//! write tests, so loading them is strict and every error points at the file,
//! line and key that needs fixing, listing all problems at once.
//!
//! - [`testdef`]: test definitions (states, parameters, monitors, loggers)
//! - [`rack`]: rack classes (instruments, identities, logical channels)
//! - [`instance`]: per-rack calibration files
//! - [`station`]: runner stations
//!
//! Parsing is YAML 1.2 (`yes`/`no`/`on`/`off` are strings; use `true`/`false`).

pub mod env;
pub mod error;
pub mod instance;
pub mod rack;
pub mod search;
pub mod station;
pub mod testdef;
mod yaml;

pub use error::{Issue, Result, SpecError};
pub use instance::{find_instance_file, load_instance, RackInstance};
pub use rack::{ChannelType, DriverRef, InstrumentConfig, LogicalChannel, RackConfig};
pub use station::StationConfig;
pub use testdef::{
    find_definition_file, load_definition, LoggerDef, LoggerKind, MonitorDef, MonitorKind,
    MonitorState, TestCaseInfo, TestDefinition,
};
