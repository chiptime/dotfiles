//! Tile configuration: parse + validate `tiles.json`.
//!
//! Source of truth lives in the dotfiles repo
//! (`tools/launchpad/config/tiles.json`), mapped to
//! `~/.config/launchpad/tiles.json` by the deploy script; `LAUNCHPAD_TILES`
//! overrides the path for tests and one-off runs.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

pub const DEFAULT_HEALTH_INTERVAL_SECS: u64 = 15;

/// Tile kind: web services get a link + health probe; scripts are catalog
/// entries with a copyable command and no probe; projects are repo catalog
/// entries (docs/location) shown in their own front section.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum TileKind {
    #[default]
    Web,
    Script,
    Project,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TileConfig {
    pub id: String,
    pub name: String,
    /// Emoji shown on the tile; the name's first letter is used when absent.
    #[serde(default)]
    pub icon: Option<String>,
    #[serde(default)]
    pub kind: TileKind,
    /// Target opened on click (web tiles); must start with `http://` or
    /// `https://` when present.
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub description: Option<String>,
    /// URL probed for health; defaults to the tile `url` (web tiles only).
    #[serde(default)]
    pub health_url: Option<String>,
    /// Seconds a probe result stays fresh; must be > 0.
    #[serde(default = "default_interval")]
    pub health_interval_secs: u64,
    /// Terminal command shown and copied on click (script tiles).
    #[serde(default)]
    pub command: Option<String>,
    /// Documentation reference shown on the tile (script tiles).
    #[serde(default)]
    pub docs: Option<String>,
    /// Where the thing lives (script tiles).
    #[serde(default)]
    pub location: Option<String>,
    /// Free-form category tags used by the front's type filter; the
    /// discoverer writes lowercase domains (proyecto, ia, teams, voz, ...).
    #[serde(default)]
    pub tags: Vec<String>,
}

fn default_interval() -> u64 {
    DEFAULT_HEALTH_INTERVAL_SECS
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct LaunchpadConfig {
    #[serde(default)]
    pub tiles: Vec<TileConfig>,
}

/// Config path: `LAUNCHPAD_TILES` override, else the XDG config home.
pub fn config_path() -> PathBuf {
    if let Ok(p) = std::env::var("LAUNCHPAD_TILES") {
        if !p.trim().is_empty() {
            return PathBuf::from(p);
        }
    }
    let base = std::env::var("XDG_CONFIG_HOME")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".config")
        });
    base.join("launchpad/tiles.json")
}

impl LaunchpadConfig {
    /// Load and validate. A missing file yields an empty config so the
    /// launchpad still serves (an empty grid) instead of erroring out.
    pub fn load_from(path: &Path) -> Result<Self, String> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let raw =
            std::fs::read_to_string(path).map_err(|e| format!("read {}: {e}", path.display()))?;
        let cfg: LaunchpadConfig =
            serde_json::from_str(&raw).map_err(|e| format!("parse {}: {e}", path.display()))?;
        cfg.validate()?;
        Ok(cfg)
    }

    pub fn load() -> Result<Self, String> {
        Self::load_from(&config_path())
    }

    fn validate(&self) -> Result<(), String> {
        let mut seen = BTreeSet::new();
        for t in &self.tiles {
            if t.id.trim().is_empty() {
                return Err(format!("tile '{}' has an empty id", t.name));
            }
            if !seen.insert(t.id.clone()) {
                return Err(format!("duplicate tile id '{}'", t.id));
            }
            if t.name.trim().is_empty() {
                return Err(format!("tile '{}' has an empty name", t.id));
            }
            if let Some(url) = &t.url {
                if !url.starts_with("http://") && !url.starts_with("https://") {
                    return Err(format!(
                        "tile '{}' url must start with http:// or https:// (got '{}')",
                        t.id, url
                    ));
                }
            }
            if t.kind == TileKind::Web && t.url.is_none() {
                return Err(format!("web tile '{}' requires a url", t.id));
            }
            if t.health_interval_secs == 0 {
                return Err(format!(
                    "tile '{}' health_interval_secs must be > 0",
                    t.id
                ));
            }
            for tag in &t.tags {
                if tag.trim().is_empty() {
                    return Err(format!("tile '{}' has an empty tag", t.id));
                }
            }
        }
        Ok(())
    }

    /// Effective health probe URL for a web tile (explicit override or the url).
    pub fn health_url(tile: &TileConfig) -> &str {
        tile.health_url
            .as_deref()
            .or(tile.url.as_deref())
            .unwrap_or("")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tile_json(id: &str, url: &str) -> String {
        format!(r#"{{"id":"{id}","name":"T {id}","url":"{url}"}}"#)
    }

    #[test]
    fn parses_tile_with_defaults() {
        let cfg: LaunchpadConfig =
            serde_json::from_str(&format!("{{\"tiles\":[{}]}}", tile_json("a", "http://x:1/")))
                .unwrap();
        assert_eq!(cfg.tiles.len(), 1);
        assert_eq!(cfg.tiles[0].health_interval_secs, DEFAULT_HEALTH_INTERVAL_SECS);
        assert_eq!(LaunchpadConfig::health_url(&cfg.tiles[0]), "http://x:1/");
        assert!(cfg.tiles[0].icon.is_none());
    }

    #[test]
    fn explicit_health_url_and_interval_win() {
        let raw = r#"{"tiles":[{"id":"a","name":"A","url":"http://x:1/",
            "health_url":"http://x:1/api/health","health_interval_secs":30}]}"#;
        let cfg: LaunchpadConfig = serde_json::from_str(raw).unwrap();
        assert_eq!(LaunchpadConfig::health_url(&cfg.tiles[0]), "http://x:1/api/health");
        assert_eq!(cfg.tiles[0].health_interval_secs, 30);
    }

    #[test]
    fn missing_file_yields_empty_config() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = LaunchpadConfig::load_from(&dir.path().join("nope.json")).unwrap();
        assert!(cfg.tiles.is_empty());
    }

    #[test]
    fn duplicate_ids_are_rejected() {
        let raw = format!(
            "{{\"tiles\":[{},{}]}}",
            tile_json("a", "http://x:1/"),
            tile_json("a", "http://x:2/")
        );
        let cfg: LaunchpadConfig = serde_json::from_str(&raw).unwrap();
        assert!(cfg.validate().unwrap_err().contains("duplicate tile id 'a'"));
    }

    #[test]
    fn non_http_url_is_rejected() {
        let cfg: LaunchpadConfig =
            serde_json::from_str(&format!("{{\"tiles\":[{}]}}", tile_json("a", "ftp://x/")))
                .unwrap();
        assert!(cfg.validate().unwrap_err().contains("must start with http"));
    }

    #[test]
    fn zero_interval_is_rejected() {
        let raw = r#"{"tiles":[{"id":"a","name":"A","url":"http://x/","health_interval_secs":0}]}"#;
        let cfg: LaunchpadConfig = serde_json::from_str(raw).unwrap();
        assert!(cfg.validate().unwrap_err().contains("health_interval_secs"));
    }

    #[test]
    fn kind_defaults_to_web() {
        let cfg: LaunchpadConfig =
            serde_json::from_str(&format!("{{\"tiles\":[{}]}}", tile_json("a", "http://x/")))
                .unwrap();
        assert_eq!(cfg.tiles[0].kind, TileKind::Web);
    }

    #[test]
    fn script_tile_parses_without_url() {
        let raw = r#"{"tiles":[{"id":"sw","name":"opencode-switch","kind":"script",
            "command":"opencode-switch","location":"scripts/opencode-switch-profile.sh"}]}"#;
        let cfg: LaunchpadConfig = serde_json::from_str(raw).unwrap();
        assert_eq!(cfg.tiles[0].kind, TileKind::Script);
        assert!(cfg.validate().is_ok());
    }

    #[test]
    fn web_tile_without_url_is_rejected() {
        let raw = r#"{"tiles":[{"id":"a","name":"A","kind":"web"}]}"#;
        let cfg: LaunchpadConfig = serde_json::from_str(raw).unwrap();
        assert!(cfg.validate().unwrap_err().contains("web tile 'a' requires a url"));
    }

    #[test]
    fn script_tile_needs_no_http_url_but_validates_when_present() {
        let raw = r#"{"tiles":[{"id":"s","name":"S","kind":"script",
            "command":"foo","url":"ftp://nope/"}]}"#;
        let cfg: LaunchpadConfig = serde_json::from_str(raw).unwrap();
        assert!(cfg.validate().unwrap_err().contains("must start with http"));
    }

    #[test]
    fn tags_parse_and_default_empty() {
        let raw = r#"{"tiles":[{"id":"a","name":"A","url":"http://x/","tags":["voz","teams"]}]}"#;
        let cfg: LaunchpadConfig = serde_json::from_str(raw).unwrap();
        assert_eq!(cfg.tiles[0].tags, vec!["voz".to_string(), "teams".to_string()]);
        let plain: LaunchpadConfig =
            serde_json::from_str(&format!("{{\"tiles\":[{}]}}", tile_json("b", "http://x:1/")))
                .unwrap();
        assert!(plain.tiles[0].tags.is_empty(), "tags default to empty");
    }

    #[test]
    fn empty_tag_is_rejected() {
        let raw = r#"{"tiles":[{"id":"a","name":"A","url":"http://x/","tags":["voz","  "]}]}"#;
        let cfg: LaunchpadConfig = serde_json::from_str(raw).unwrap();
        assert!(cfg.validate().unwrap_err().contains("empty tag"));
    }

    #[test]
    fn project_kind_parses_without_url() {
        let raw = r#"{"tiles":[{"id":"p","name":"P","kind":"project",
            "docs":"~/Code/personal/p/README.md","location":"~/Code/personal/p"}]}"#;
        let cfg: LaunchpadConfig = serde_json::from_str(raw).unwrap();
        assert_eq!(cfg.tiles[0].kind, TileKind::Project);
        assert!(cfg.validate().is_ok(), "project tiles need no url");
    }
}
