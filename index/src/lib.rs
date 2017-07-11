extern crate bit_set;
extern crate byteorder;
#[macro_use]
extern crate error_chain;
extern crate libc;
extern crate lz4;
extern crate memmap;
extern crate regex_syntax;

pub mod find;
mod search;
mod tri;

pub use tri::trigrams_full;
pub use tri::explain_packed;

#[cfg(test)]
mod tests;

mod errors {
    error_chain! {
        foreign_links {
            Io(::std::io::Error);
        }
    }
}
