//! The shapes of the catalogue: what a constraint looks like, without its variables.

use serde_json::Value;
use std::path::{Path, PathBuf};

/// One constraint shape, as written by `tools/build_catalogue.py`.
#[derive(Clone, Debug)]
pub struct Shape {
    /// Position in the catalogue; stable for a given catalogue.
    pub id: usize,
    /// A hash of the shape's content, stable when the catalogue grows; empty if the catalogue
    /// has none. It seeds what a shape draws at random (the guides of a disjunctive).
    pub key: String,
    /// `all_different`, `not_equal`, `sum`, `among`, `at_least`, `gcc`, `regular`, `table`,
    /// `disjunctive`, or a kind that `aicad` does not have.
    pub kind: String,
    /// True if the kind is listed in `supported_constraints.json`.
    pub supported: bool,
    /// Number of variables in the scope.
    pub arity: usize,
    /// Number of distinct values in the union of the domains.
    pub u: usize,
    /// Smallest value of the domains.
    pub lo: isize,
    /// Parameters of the kind (target, values, bounds, automaton, ...).
    pub params: Value,
    /// The directory of the catalogue file, where files that a shape refers to live (the tuples
    /// of a table are in `tables/`).
    pub base_dir: PathBuf,
}

impl Shape {
    /// A short name that identifies the shape in a benchmark report.
    pub fn label(&self) -> String {
        format!("{}#{}/n{}/u{}", self.kind, self.id, self.arity, self.u)
    }
}

/// Reads the shapes of a catalogue file. Shapes whose domain is unknown or empty are left out.
pub fn load(path: impl AsRef<Path>) -> Result<Vec<Shape>, String> {
    let path = path.as_ref();
    let text = std::fs::read_to_string(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let doc: Value = serde_json::from_str(&text).map_err(|e| format!("{}: invalid JSON: {e}", path.display()))?;
    let list = doc["shapes"].as_array().ok_or_else(|| format!("{}: no `shapes` array", path.display()))?;
    let mut out = Vec::new();
    for s in list {
        let (Some(id), Some(kind), Some(arity)) = (s["id"].as_u64(), s["kind"].as_str(), s["arity"].as_u64()) else {
            return Err(format!("{}: a shape lacks id, kind or arity", path.display()));
        };
        let (Some(u), Some(lo)) = (s["domain"]["u"].as_u64(), s["domain"]["lo"].as_i64()) else {
            continue;
        };
        if u == 0 {
            continue;
        }
        out.push(Shape {
            id: id as usize,
            key: s["key"].as_str().unwrap_or("").to_string(),
            kind: kind.to_string(),
            supported: s["supported"].as_bool().unwrap_or(false),
            arity: arity as usize,
            u: u as usize,
            lo: lo as isize,
            params: s["params"].clone(),
            base_dir: path.parent().map(Path::to_path_buf).unwrap_or_default(),
        });
    }
    Ok(out)
}
