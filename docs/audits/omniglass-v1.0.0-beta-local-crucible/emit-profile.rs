//! Faithful profile emitter for the OmniGlass v1.0.0-beta sandbox audit.
//!
//! This does NOT re-implement the profile generator. It `include!`s the
//! ACTUAL product source files byte-for-byte, so the emitted `.sb` profile
//! is exactly what OmniGlass generates at runtime:
//!   - src/mcp/manifest.rs
//!   - src/mcp/sandbox/macos.rs
//!
//! Usage: emit-profile <plugin_dir> <out_profile_path>

mod mcp {
    #[path = "/Users/localuser/Desktop/NARS/OmniGlass/src-tauri/src/mcp/manifest.rs"]
    pub mod manifest;
    pub mod sandbox {
        #[path = "/Users/localuser/Desktop/NARS/OmniGlass/src-tauri/src/mcp/sandbox/macos.rs"]
        pub mod macos;
    }
}

use mcp::manifest::{FsPerm, Permissions, PluginManifest, Runtime, ShellPerm};
use std::path::PathBuf;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let plugin_dir = PathBuf::from(&args[1]);
    let out = PathBuf::from(&args[2]);

    // Representative "worst-case but valid" plugin: broad shell allowlist
    // (maximum attacker rope), one approved read-write path, NO network.
    // Network is intentionally omitted: the profile's network rule is
    // coarse all-or-nothing (see macos.rs comment), so a network-enabled
    // plugin would have ALL outbound allowed. Auditing the no-network
    // profile is what makes the V3 "outbound denied" property meaningful.
    let cmds: Vec<String> = [
        "cat", "ls", "echo", "cp", "ln", "env", "grep", "nc", "sqlite3",
        "security", "lldb", "osascript", "sudo", "launchctl", "kextload",
        "chmod", "screencapture", "kill", "df", "curl", "printf", "tee",
        "python3", "bash", "sh", "head", "pgrep", "whoami", "open", "mkdir",
    ].iter().map(|s| s.to_string()).collect();

    let manifest = PluginManifest {
        id: "com.audit.crucible".to_string(),
        name: "Audit Crucible".to_string(),
        version: "1.0.0".to_string(),
        description: String::new(),
        runtime: Runtime::Node,
        entry: "index.js".to_string(),
        permissions: Permissions {
            clipboard: false,
            network: None,
            filesystem: Some(vec![FsPerm {
                path: "/private/tmp/omni-glass-approved".to_string(),
                access: "read-write".to_string(),
            }]),
            environment: None,
            shell: Some(ShellPerm { commands: cmds }),
        },
        configuration: None,
    };

    match mcp::sandbox::macos::generate_profile(&manifest, &plugin_dir) {
        Ok(profile) => {
            std::fs::write(&out, &profile).expect("write profile");
            eprintln!("Wrote {} bytes to {}", profile.len(), out.display());
        }
        Err(e) => {
            eprintln!("generate_profile failed: {}", e);
            std::process::exit(1);
        }
    }
}
