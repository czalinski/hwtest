//! Runtime binding to the vendor C library `libdaqhats`.
//!
//! This is the only module allowed to use `unsafe`: calling C functions
//! through `libloading` is inherently unsafe. Every call passes valid
//! pointers to locals or to buffers whose length is passed alongside, matching
//! the prototypes in `daqhats/include/*.h`.
#![allow(unsafe_code)]

use std::ffi::{c_char, c_int, CStr};

use libloading::{Library, Symbol};

use super::{check, DaqHats, HatInfo, ScanRead};
use crate::error::{InstrumentError, Result};

/// `struct HatInfo` from `daqhats.h`.
#[repr(C)]
struct RawHatInfo {
    address: u8,
    id: u16,
    version: u16,
    product_name: [c_char; 256],
}

/// The loaded library.
pub struct LibDaqHats {
    lib: Library,
}

const CANDIDATES: [&str; 3] = ["libdaqhats.so.1", "libdaqhats.so", "/usr/local/lib/libdaqhats.so"];

impl LibDaqHats {
    /// Loads `libdaqhats`.
    ///
    /// # Errors
    /// [`InstrumentError::Library`] if it is not installed.
    pub fn load() -> Result<Self> {
        let mut last = String::new();
        for name in CANDIDATES {
            // SAFETY: loading a shared library runs its initialisers; libdaqhats
            // has no constructors with preconditions.
            match unsafe { Library::new(name) } {
                Ok(lib) => return Ok(Self { lib }),
                Err(e) => last = e.to_string(),
            }
        }
        Err(InstrumentError::Library(format!(
            "libdaqhats not found ({last}). Install it on the Pi: git clone https://github.com/mccdaq/daqhats && cd daqhats && sudo ./install.sh"
        )))
    }

    fn sym<T>(&self, name: &str) -> Result<Symbol<'_, T>> {
        // SAFETY: `T` is always the function pointer type of the C prototype
        // with this name (see the call sites below).
        unsafe { self.lib.get::<T>(name) }.map_err(|e| {
            InstrumentError::Library(format!("libdaqhats: missing symbol {name}: {e}"))
        })
    }

    fn call_addr(&self, name: &str, address: u8) -> Result<()> {
        let f = self.sym::<unsafe extern "C" fn(u8) -> c_int>(name)?;
        // SAFETY: prototype `int name(uint8_t address)`.
        check(unsafe { f(address) }, || format!("{name}(address={address})"))
    }

    fn serial(&self, name: &str, address: u8) -> Result<String> {
        let f = self.sym::<unsafe extern "C" fn(u8, *mut c_char) -> c_int>(name)?;
        let mut buf = [0 as c_char; 64]; // library writes at most 9 bytes
                                         // SAFETY: prototype `int name(uint8_t, char* buffer)`; buffer is large enough.
        check(unsafe { f(address, buf.as_mut_ptr()) }, || format!("{name}(address={address})"))?;
        // SAFETY: the library NUL-terminates; the buffer is zero-initialised.
        Ok(unsafe { CStr::from_ptr(buf.as_ptr()) }.to_string_lossy().into_owned())
    }

    fn read_f64(&self, name: &str, address: u8, channel: u8, with_options: bool) -> Result<f64> {
        let mut v = 0.0f64;
        let code = if with_options {
            let f = self.sym::<unsafe extern "C" fn(u8, u8, u32, *mut f64) -> c_int>(name)?;
            // SAFETY: prototype `int name(uint8_t, uint8_t, uint32_t options, double*)`.
            unsafe { f(address, channel, 0, &raw mut v) }
        } else {
            let f = self.sym::<unsafe extern "C" fn(u8, u8, *mut f64) -> c_int>(name)?;
            // SAFETY: prototype `int name(uint8_t, uint8_t, double*)`.
            unsafe { f(address, channel, &raw mut v) }
        };
        check(code, || format!("{name}(address={address}, channel={channel})"))?;
        Ok(v)
    }
}

impl DaqHats for LibDaqHats {
    fn list(&self) -> Result<Vec<HatInfo>> {
        let f = self.sym::<unsafe extern "C" fn(u16, *mut RawHatInfo) -> c_int>("hat_list")?;
        // SAFETY: with a null list, hat_list only returns the count.
        let n = unsafe { f(0, std::ptr::null_mut()) };
        let Ok(count) = usize::try_from(n) else { return Ok(Vec::new()) };
        let mut raw: Vec<RawHatInfo> = (0..count)
            .map(|_| RawHatInfo { address: 0, id: 0, version: 0, product_name: [0; 256] })
            .collect();
        // SAFETY: `raw` holds `count` entries, as returned by the previous call.
        let n = unsafe { f(0, raw.as_mut_ptr()) };
        raw.truncate(usize::try_from(n).unwrap_or(0));
        Ok(raw
            .iter()
            .map(|r| HatInfo {
                address: r.address,
                id: r.id,
                version: r.version,
                // SAFETY: product_name is a NUL-terminated C string filled by the library.
                product_name: unsafe { CStr::from_ptr(r.product_name.as_ptr()) }
                    .to_string_lossy()
                    .into_owned(),
            })
            .collect())
    }

    fn mcc118_open(&self, address: u8) -> Result<()> {
        self.call_addr("mcc118_open", address)
    }
    fn mcc118_close(&self, address: u8) -> Result<()> {
        self.call_addr("mcc118_close", address)
    }
    fn mcc118_serial(&self, address: u8) -> Result<String> {
        self.serial("mcc118_serial", address)
    }
    fn mcc118_a_in_read(&self, address: u8, channel: u8) -> Result<f64> {
        self.read_f64("mcc118_a_in_read", address, channel, true)
    }
    fn mcc118_scan_actual_rate(&self, channel_count: u8, rate: f64) -> Result<f64> {
        let f = self.sym::<unsafe extern "C" fn(u8, f64, *mut f64) -> c_int>(
            "mcc118_a_in_scan_actual_rate",
        )?;
        let mut actual = 0.0f64;
        // SAFETY: prototype `int (uint8_t channel_count, double rate, double* actual)`.
        check(unsafe { f(channel_count, rate, &raw mut actual) }, || {
            "mcc118_a_in_scan_actual_rate".into()
        })?;
        Ok(actual)
    }
    fn mcc118_scan_start(
        &self,
        address: u8,
        channel_mask: u8,
        samples_per_channel: u32,
        rate: f64,
        options: u32,
    ) -> Result<()> {
        let f = self.sym::<unsafe extern "C" fn(u8, u8, u32, f64, u32) -> c_int>(
            "mcc118_a_in_scan_start",
        )?;
        // SAFETY: prototype `int (uint8_t, uint8_t mask, uint32_t samples, double rate, uint32_t options)`.
        check(unsafe { f(address, channel_mask, samples_per_channel, rate, options) }, || {
            format!("mcc118_a_in_scan_start(address={address})")
        })
    }
    fn mcc118_scan_read(
        &self,
        address: u8,
        samples_per_channel: i32,
        timeout: f64,
        buffer: &mut [f64],
    ) -> Result<ScanRead> {
        let f = self.sym::<unsafe extern "C" fn(
            u8,
            *mut u16,
            i32,
            f64,
            *mut f64,
            u32,
            *mut u32,
        ) -> c_int>("mcc118_a_in_scan_read")?;
        let mut status = 0u16;
        let mut read = 0u32;
        let len = u32::try_from(buffer.len()).unwrap_or(u32::MAX);
        // SAFETY: prototype `int (uint8_t, uint16_t* status, int32_t samples, double timeout,
        // double* buffer, uint32_t buffer_size_samples, uint32_t* read)`; `len` is the buffer length.
        let code = unsafe {
            f(
                address,
                &raw mut status,
                samples_per_channel,
                timeout,
                buffer.as_mut_ptr(),
                len,
                &raw mut read,
            )
        };
        check(code, || format!("mcc118_a_in_scan_read(address={address})"))?;
        Ok(ScanRead { status, samples_per_channel: read })
    }
    fn mcc118_scan_stop(&self, address: u8) -> Result<()> {
        self.call_addr("mcc118_a_in_scan_stop", address)
    }
    fn mcc118_scan_cleanup(&self, address: u8) -> Result<()> {
        self.call_addr("mcc118_a_in_scan_cleanup", address)
    }

    fn mcc134_open(&self, address: u8) -> Result<()> {
        self.call_addr("mcc134_open", address)
    }
    fn mcc134_close(&self, address: u8) -> Result<()> {
        self.call_addr("mcc134_close", address)
    }
    fn mcc134_serial(&self, address: u8) -> Result<String> {
        self.serial("mcc134_serial", address)
    }
    fn mcc134_tc_type_write(&self, address: u8, channel: u8, tc_type: u8) -> Result<()> {
        let f = self.sym::<unsafe extern "C" fn(u8, u8, u8) -> c_int>("mcc134_tc_type_write")?;
        // SAFETY: prototype `int (uint8_t, uint8_t channel, uint8_t type)`.
        check(unsafe { f(address, channel, tc_type) }, || {
            format!("mcc134_tc_type_write(address={address}, channel={channel})")
        })
    }
    fn mcc134_update_interval_write(&self, address: u8, seconds: u8) -> Result<()> {
        let f =
            self.sym::<unsafe extern "C" fn(u8, u8) -> c_int>("mcc134_update_interval_write")?;
        // SAFETY: prototype `int (uint8_t, uint8_t interval)`.
        check(unsafe { f(address, seconds) }, || {
            format!("mcc134_update_interval_write(address={address})")
        })
    }
    fn mcc134_t_in_read(&self, address: u8, channel: u8) -> Result<f64> {
        self.read_f64("mcc134_t_in_read", address, channel, false)
    }

    fn mcc152_open(&self, address: u8) -> Result<()> {
        self.call_addr("mcc152_open", address)
    }
    fn mcc152_close(&self, address: u8) -> Result<()> {
        self.call_addr("mcc152_close", address)
    }
    fn mcc152_serial(&self, address: u8) -> Result<String> {
        self.serial("mcc152_serial", address)
    }
    fn mcc152_dio_config_write_bit(
        &self,
        address: u8,
        channel: u8,
        item: u8,
        value: u8,
    ) -> Result<()> {
        let f = self
            .sym::<unsafe extern "C" fn(u8, u8, u8, u8) -> c_int>("mcc152_dio_config_write_bit")?;
        // SAFETY: prototype `int (uint8_t, uint8_t channel, uint8_t item, uint8_t value)`.
        check(unsafe { f(address, channel, item, value) }, || {
            format!("mcc152_dio_config_write_bit(address={address}, channel={channel})")
        })
    }
    fn mcc152_dio_input_read_bit(&self, address: u8, channel: u8) -> Result<bool> {
        let f = self
            .sym::<unsafe extern "C" fn(u8, u8, *mut u8) -> c_int>("mcc152_dio_input_read_bit")?;
        let mut v = 0u8;
        // SAFETY: prototype `int (uint8_t, uint8_t channel, uint8_t* value)`.
        check(unsafe { f(address, channel, &raw mut v) }, || {
            format!("mcc152_dio_input_read_bit(address={address}, channel={channel})")
        })?;
        Ok(v != 0)
    }
    fn mcc152_dio_output_write_bit(&self, address: u8, channel: u8, value: bool) -> Result<()> {
        let f =
            self.sym::<unsafe extern "C" fn(u8, u8, u8) -> c_int>("mcc152_dio_output_write_bit")?;
        // SAFETY: prototype `int (uint8_t, uint8_t channel, uint8_t value)`.
        check(unsafe { f(address, channel, u8::from(value)) }, || {
            format!("mcc152_dio_output_write_bit(address={address}, channel={channel})")
        })
    }
    fn mcc152_a_out_write(&self, address: u8, channel: u8, value: f64) -> Result<()> {
        let f =
            self.sym::<unsafe extern "C" fn(u8, u8, u32, f64) -> c_int>("mcc152_a_out_write")?;
        // SAFETY: prototype `int (uint8_t, uint8_t channel, uint32_t options, double value)`.
        check(unsafe { f(address, channel, 0, value) }, || {
            format!("mcc152_a_out_write(address={address}, channel={channel})")
        })
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn hat_info_layout_matches_c() {
        // uint8 + pad + uint16 + uint16 + char[256] = 262 bytes, 2-byte aligned.
        assert_eq!(std::mem::size_of::<super::RawHatInfo>(), 262);
        assert_eq!(std::mem::align_of::<super::RawHatInfo>(), 2);
    }

    #[test]
    fn missing_library_is_explained() {
        if let Err(e) = super::LibDaqHats::load() {
            assert!(e.to_string().contains("install.sh"));
        }
    }
}
