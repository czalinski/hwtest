//! The test run engine behind the Python SDK and the runner.
//!
//! A [`Session`] owns everything a test run needs besides the engineer's
//! hardware steps: the test definition, the rack, monitors, loggers, NATS
//! publishing, the results database and progress events. It is driven as a
//! stepper so a callback (the Python test case) can run between steps without
//! holding the session:
//!
//! ```no_run
//! # use hwtest_run::{Session, RunOptions, Step, StepOutcome};
//! # fn demo(mut s: Session) {
//! while let Step::Run(state) = s.next_step() {
//!     // ... drive hardware for `state`, calling s.measure(...) ...
//!     s.complete_step(StepOutcome::Ok);
//! }
//! let summary = s.summary();
//! # }
//! ```
//!
//! Modes: `functional` runs `state_sequence` once; `hass` and `halt` repeat it
//! until the first monitor failure, a stop request, or `max_cycles`.

pub mod events;
mod session;

pub use events::{parse_event_line, Event, EventSink, EVENT_PREFIX};
pub use session::{
    Mode, Outcome, RunError, RunOptions, RunStats, RunSummary, Session, SharedRack, Step,
    StepOutcome,
};
