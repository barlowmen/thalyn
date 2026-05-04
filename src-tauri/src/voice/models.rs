//! Whisper model catalog + on-disk store.
//!
//! ADR-0025 pins three GGML model variants for the local engine:
//! `tiny.en` (78 MB) as the floor for pre-AVX2 / weak hardware,
//! `base.en` (148 MB) as the immediate-first-use preload, and
//! `small.en` (487 MB) as the default once the lazy-download path
//! has populated it. The variants share a common upstream URL
//! template under `huggingface.co/ggerganov/whisper.cpp` and pin
//! SHA-256 digests so the download path (lands next) can verify
//! integrity before swapping a half-written file in.
//!
//! The lazy-download path itself isn't here yet — the catalog and
//! path resolver land first so the next commit can wire HTTP +
//! progress without touching every caller. AppState already uses
//! [`ModelStore::try_load_default`] to pick the best available
//! model on disk; until a model is present, the manager falls back
//! to the noop engine.

use std::path::{Path, PathBuf};

/// One model variant in the catalog. The variants are listed
/// largest-first because [`ModelStore::try_load_default`] picks the
/// first present one — when both `small.en` and `base.en` are on
/// disk, `small.en` wins.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[allow(clippy::enum_variant_names)]
pub enum ModelVariant {
    /// Default once the lazy-download path is wired. 487 MB.
    SmallEn,
    /// Preload candidate, low-RAM fallback. 148 MB.
    BaseEn,
    /// Floor for pre-AVX2 x86 / weak Windows laptops. 78 MB.
    TinyEn,
}

impl ModelVariant {
    /// Order from highest-quality / largest to smallest. The
    /// hardware probe (next commit) walks this list and benchmarks
    /// each candidate; [`ModelStore::try_load_default`] picks the
    /// first variant that's on disk.
    pub const ORDERED: &'static [ModelVariant] = &[
        ModelVariant::SmallEn,
        ModelVariant::BaseEn,
        ModelVariant::TinyEn,
    ];

    /// Filename on disk under `<data_dir>/models/`. Whisper.cpp's
    /// upstream naming convention is `ggml-<variant>.bin`; the file
    /// in our data dir matches so a power user can drop a manually
    /// downloaded model in without renaming.
    pub fn filename(self) -> &'static str {
        match self {
            ModelVariant::SmallEn => "ggml-small.en.bin",
            ModelVariant::BaseEn => "ggml-base.en.bin",
            ModelVariant::TinyEn => "ggml-tiny.en.bin",
        }
    }

    /// Approximate on-disk size, used for progress reporting and
    /// the hardware probe's RAM check. The download path that
    /// consumes this lands in the next commit.
    #[allow(dead_code)]
    pub fn size_bytes(self) -> u64 {
        match self {
            ModelVariant::SmallEn => 487_000_000,
            ModelVariant::BaseEn => 148_000_000,
            ModelVariant::TinyEn => 78_000_000,
        }
    }

    /// Upstream URL the lazy-download path fetches from. All three
    /// variants live under the same Hugging Face mirror Whisper.cpp
    /// itself uses; the download commit will pin this and
    /// `expected_sha256()` together. Consumed by the next commit
    /// that wires the HTTP client.
    #[allow(dead_code)]
    pub fn download_url(self) -> &'static str {
        match self {
            ModelVariant::SmallEn => {
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
            }
            ModelVariant::BaseEn => {
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
            }
            ModelVariant::TinyEn => {
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin"
            }
        }
    }

    /// Pinned SHA-256 of the upstream `.bin` file. Verified on
    /// download before the file is renamed into place. Matches
    /// `ggerganov/whisper.cpp`'s `models/download-ggml-model.sh`
    /// SHA list as of 2026-05. Consumed by the download path.
    #[allow(dead_code)]
    pub fn expected_sha256(self) -> &'static str {
        match self {
            ModelVariant::SmallEn => {
                "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b"
            }
            ModelVariant::BaseEn => {
                "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002"
            }
            ModelVariant::TinyEn => {
                "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f"
            }
        }
    }

    /// Human-readable label for the model — surfaced in settings
    /// UI (later commits) and the startup log line.
    pub fn label(self) -> &'static str {
        match self {
            ModelVariant::SmallEn => "small.en",
            ModelVariant::BaseEn => "base.en",
            ModelVariant::TinyEn => "tiny.en",
        }
    }
}

/// On-disk store for Whisper models. Owns one root directory
/// (`<data_dir>/models/`); the lazy-download path writes into it,
/// the engine reads from it.
#[derive(Debug, Clone)]
pub struct ModelStore {
    root: PathBuf,
}

impl ModelStore {
    /// Build a store rooted under `<data_dir>/models/`. Doesn't
    /// create the directory; the download path does that lazily.
    pub fn new(data_dir: impl Into<PathBuf>) -> Self {
        Self {
            root: data_dir.into().join("models"),
        }
    }

    /// Resolve the on-disk path for one variant.
    pub fn path_for(&self, variant: ModelVariant) -> PathBuf {
        self.root.join(variant.filename())
    }

    /// Whether the file for `variant` exists and is non-empty.
    pub fn is_present(&self, variant: ModelVariant) -> bool {
        let path = self.path_for(variant);
        std::fs::metadata(&path)
            .map(|m| m.is_file() && m.len() > 0)
            .unwrap_or(false)
    }

    /// Pick the largest variant that's present on disk. Returns
    /// `None` when no model is downloaded yet — callers fall back
    /// to the noop engine until the lazy-download path runs.
    pub fn try_load_default(&self) -> Option<(ModelVariant, PathBuf)> {
        ModelVariant::ORDERED
            .iter()
            .copied()
            .find(|v| self.is_present(*v))
            .map(|v| (v, self.path_for(v)))
    }

    /// Read-only view of the root directory.
    #[allow(dead_code)]
    pub fn root(&self) -> &Path {
        &self.root
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ordered_lists_largest_first() {
        assert_eq!(ModelVariant::ORDERED[0], ModelVariant::SmallEn);
        assert_eq!(ModelVariant::ORDERED[1], ModelVariant::BaseEn);
        assert_eq!(ModelVariant::ORDERED[2], ModelVariant::TinyEn);
    }

    #[test]
    fn filenames_match_whisper_cpp_convention() {
        assert_eq!(ModelVariant::SmallEn.filename(), "ggml-small.en.bin");
        assert_eq!(ModelVariant::BaseEn.filename(), "ggml-base.en.bin");
        assert_eq!(ModelVariant::TinyEn.filename(), "ggml-tiny.en.bin");
    }

    #[test]
    fn download_urls_point_at_hugging_face_mirror() {
        for variant in ModelVariant::ORDERED {
            assert!(variant
                .download_url()
                .starts_with("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"));
        }
    }

    #[test]
    fn expected_sha256_is_a_64_char_hex_string() {
        for variant in ModelVariant::ORDERED {
            let sha = variant.expected_sha256();
            assert_eq!(sha.len(), 64, "{} sha256 length", variant.label());
            assert!(
                sha.chars().all(|c| c.is_ascii_hexdigit()),
                "{} sha256 hex",
                variant.label()
            );
        }
    }

    #[test]
    fn path_for_uses_models_subdir_under_data_dir() {
        let store = ModelStore::new("/tmp/data");
        assert_eq!(
            store.path_for(ModelVariant::BaseEn),
            PathBuf::from("/tmp/data/models/ggml-base.en.bin")
        );
    }

    #[test]
    fn try_load_default_returns_none_when_no_files_present() {
        let dir = tempfile::tempdir().unwrap();
        let store = ModelStore::new(dir.path());
        assert!(store.try_load_default().is_none());
    }

    #[test]
    fn try_load_default_picks_largest_present_variant() {
        let dir = tempfile::tempdir().unwrap();
        let store = ModelStore::new(dir.path());
        std::fs::create_dir_all(store.root()).unwrap();
        // Drop both base.en and tiny.en into the dir; expect base.en
        // to win because it's larger.
        std::fs::write(store.path_for(ModelVariant::TinyEn), b"x").unwrap();
        std::fs::write(store.path_for(ModelVariant::BaseEn), b"x").unwrap();
        let (variant, path) = store.try_load_default().expect("default resolves");
        assert_eq!(variant, ModelVariant::BaseEn);
        assert_eq!(path, store.path_for(ModelVariant::BaseEn));
    }

    #[test]
    fn try_load_default_skips_empty_files() {
        let dir = tempfile::tempdir().unwrap();
        let store = ModelStore::new(dir.path());
        std::fs::create_dir_all(store.root()).unwrap();
        // A zero-byte file from a half-completed download must not
        // be picked up — is_present requires the file to be > 0.
        std::fs::write(store.path_for(ModelVariant::SmallEn), b"").unwrap();
        std::fs::write(store.path_for(ModelVariant::BaseEn), b"x").unwrap();
        let (variant, _) = store.try_load_default().unwrap();
        assert_eq!(variant, ModelVariant::BaseEn);
    }
}
