//! Declarative user configuration for display order, capacity, file providers, and labels.
//!
//! Read from `AI_QUOTAS_CONFIG`, else `~/.config/ai-quotas/config.json`, else `config.json`.
//! If absent or unparseable, built-in defaults are used seamlessly.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConfiguredFileProvider {
    pub id: String,
    pub display_name: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AppConfig {
    #[serde(default = "default_order")]
    pub order: Vec<String>,
    #[serde(default = "default_capacity")]
    pub capacity: BTreeMap<String, f64>,
    #[serde(default = "default_file_providers")]
    pub file_providers: Vec<ConfiguredFileProvider>,
    #[serde(default = "default_main_labels")]
    pub main_labels: Vec<String>,
    #[serde(default = "default_weekly_labels")]
    pub weekly_labels: BTreeMap<String, String>,
}

pub fn default_order() -> Vec<String> {
    vec![
        "claude".into(),
        "zai".into(),
        "chatgpt".into(),
        "opencode".into(),
        "gemini".into(),
        "gemini-3p".into(),
        "deepseek".into(),
    ]
}

pub fn default_capacity() -> BTreeMap<String, f64> {
    let mut m = BTreeMap::new();
    m.insert("claude".into(), 1.0);
    m.insert("zai".into(), 20.0);
    m.insert("chatgpt".into(), 1.0);
    m.insert("opencode".into(), 1.0);
    m.insert("gemini".into(), 1.0);
    m.insert("gemini-3p".into(), 1.0);
    m
}

pub fn default_file_providers() -> Vec<ConfiguredFileProvider> {
    vec![
        ConfiguredFileProvider {
            id: "gemini".into(),
            display_name: "Google Gemini".into(),
        },
        ConfiguredFileProvider {
            id: "gemini-3p".into(),
            display_name: "Antigravity (Claude)".into(),
        },
    ]
}

pub fn default_main_labels() -> Vec<String> {
    vec![
        "5h window".into(),
        "weekly".into(),
        "monthly".into(),
        "daily".into(),
        "3p 5h window".into(),
        "3p weekly".into(),
    ]
}

pub fn default_weekly_labels() -> BTreeMap<String, String> {
    let mut m = BTreeMap::new();
    m.insert("gemini-3p".into(), "3p weekly".into());
    m
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            order: default_order(),
            capacity: default_capacity(),
            file_providers: default_file_providers(),
            main_labels: default_main_labels(),
            weekly_labels: default_weekly_labels(),
        }
    }
}

pub fn config_path() -> PathBuf {
    if let Some(path) = std::env::var_os("AI_QUOTAS_CONFIG").filter(|p| !p.is_empty()) {
        return PathBuf::from(path);
    }
    match std::env::var_os("HOME").filter(|h| !h.is_empty()) {
        Some(home) => PathBuf::from(home).join(".config").join("ai-quotas").join("config.json"),
        _ => PathBuf::from("config.json"),
    }
}

impl AppConfig {
    /// Load config from the resolved path, falling back to defaults on error/absence.
    pub fn load() -> Self {
        Self::load_from(&config_path())
    }

    /// Pure loader from an explicit path.
    pub fn load_from(path: &Path) -> Self {
        let bytes = match std::fs::read(path) {
            Ok(b) => b,
            Err(_) => return Self::default(),
        };
        serde_json::from_slice(&bytes).unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::NamedTempFile;
    use std::io::Write;

    #[test]
    fn defaults_are_well_formed() {
        let cfg = AppConfig::default();
        assert!(cfg.order.contains(&"gemini-3p".to_string()));
        assert_eq!(cfg.capacity.get("gemini-3p"), Some(&1.0));
        assert!(cfg.file_providers.iter().any(|p| p.id == "gemini-3p" && p.display_name == "Antigravity (Claude)"));
        assert!(cfg.main_labels.contains(&"3p 5h window".to_string()));
        assert_eq!(cfg.weekly_labels.get("gemini-3p"), Some(&"3p weekly".to_string()));
    }

    #[test]
    fn loads_custom_config() {
        let mut f = NamedTempFile::new().unwrap();
        let custom_json = r#"{
            "order": ["mistral", "claude"],
            "capacity": { "mistral": 2.0 },
            "file_providers": [
                { "id": "mistral", "display_name": "Mistral Le Chat" }
            ],
            "main_labels": ["12h window"]
        }"#;
        f.write_all(custom_json.as_bytes()).unwrap();

        let cfg = AppConfig::load_from(f.path());
        assert_eq!(cfg.order, vec!["mistral", "claude"]);
        assert_eq!(cfg.capacity.get("mistral"), Some(&2.0));
        assert_eq!(cfg.file_providers.len(), 1);
        assert_eq!(cfg.file_providers[0].id, "mistral");
        assert_eq!(cfg.file_providers[0].display_name, "Mistral Le Chat");
        assert_eq!(cfg.main_labels, vec!["12h window"]);
        assert_eq!(cfg.weekly_labels.get("gemini-3p"), Some(&"3p weekly".to_string())); // default preserved
    }

    #[test]
    fn absent_file_returns_default() {
        let cfg = AppConfig::load_from(Path::new("/nonexistent-ai-quotas-config.json"));
        assert_eq!(cfg, AppConfig::default());
    }
}
