//! A tiny seeded generator, so that the workloads do not depend on the version of a random crate.

/// SplitMix64: fast, full period, good enough to draw paths.
#[derive(Clone, Debug)]
pub struct Rng(u64);

impl Rng {
    /// A generator that always yields the same sequence for the same `seed`.
    pub fn new(seed: u64) -> Self {
        Rng(seed)
    }

    /// The next 64 random bits.
    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// A uniform index in `0..n`. Panics if `n` is 0.
    pub fn below(&mut self, n: usize) -> usize {
        assert!(n > 0, "Rng::below needs a non-empty range");
        ((self.next_u64() as u128 * n as u128) >> 64) as usize
    }
}
