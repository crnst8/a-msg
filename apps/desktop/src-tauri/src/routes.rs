//! The renderer can name a route; it cannot supply a URL. Keep in step with
//! src/transport/operations.ts (`scripts/check` compares the two tables).

pub const ROUTES: &[(&str, &str, &str)] = &[
    ("health", "GET", "/v1/health"),
    ("capabilities", "GET", "/v1/capabilities"),
    ("snapshot", "GET", "/v1/snapshot"),
    ("listSpaces", "GET", "/v1/spaces"),
    ("createSpace", "POST", "/v1/spaces"),
    ("patchSpace", "PATCH", "/v1/spaces/{id}"),
    ("readSpace", "POST", "/v1/spaces/{id}/read"),
    ("listMessages", "GET", "/v1/spaces/{id}/messages"),
    ("fork", "POST", "/v1/spaces/{id}/fork"),
    ("archiveSpace", "POST", "/v1/spaces/{id}/archive"),
    ("restoreSpace", "POST", "/v1/spaces/{id}/restore"),
    ("deleteSpace", "POST", "/v1/spaces/{id}/delete"),
    ("getOperation", "GET", "/v1/operations/{id}"),
    ("retryOperation", "POST", "/v1/operations/{id}/retry"),
    ("dismissOperation", "POST", "/v1/operations/{id}/dismiss"),
    ("cancelRun", "POST", "/v1/runs/{id}/cancel"),
    ("retryRun", "POST", "/v1/runs/{id}/retry"),
    ("getQueued", "GET", "/v1/queued-messages/{id}"),
    ("patchQueued", "PATCH", "/v1/queued-messages/{id}"),
    ("deleteQueued", "DELETE", "/v1/queued-messages/{id}"),
    ("listArtifacts", "GET", "/v1/artifacts"),
    ("getArtifact", "GET", "/v1/artifacts/{id}"),
    ("hideArtifact", "POST", "/v1/artifacts/{id}/hide"),
    ("unhideArtifact", "POST", "/v1/artifacts/{id}/unhide"),
    ("deleteArtifact", "DELETE", "/v1/artifacts/{id}"),
    ("search", "GET", "/v1/search"),
    ("listConnections", "GET", "/v1/connections"),
    ("createConnection", "POST", "/v1/connections"),
    ("testConnection", "POST", "/v1/connections/{id}/test"),
    ("readConfig", "GET", "/v1/connections/{id}/config"),
    (
        "proposePatch",
        "POST",
        "/v1/connections/{id}/config-patches",
    ),
    ("applyPatch", "POST", "/v1/config-patches/{id}/apply"),
    ("rollbackPatch", "POST", "/v1/config-patches/{id}/rollback"),
    ("listAutomations", "GET", "/v1/automations"),
    ("automationAction", "POST", "/v1/automations/{id}/actions"),
    ("listDevices", "GET", "/v1/devices"),
    ("revokeDevice", "DELETE", "/v1/devices/{id}"),
];

pub fn resolve(name: &str) -> Option<(&'static str, &'static str)> {
    ROUTES
        .iter()
        .find(|(route, _, _)| *route == name)
        .map(|(_, method, template)| (*method, *template))
}

/// IDs are UUIDs. Anything else could steer the template somewhere else.
pub fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_routes_are_refused() {
        assert!(resolve("listSpaces").is_some());
        assert!(resolve("/v1/spaces").is_none());
        assert!(resolve("connectorDispatch").is_none());
    }

    #[test]
    fn connector_and_pairing_routes_are_not_reachable() {
        for (_, _, template) in ROUTES {
            assert!(!template.contains("connector"));
            assert!(!template.contains("pair"));
        }
    }

    #[test]
    fn ids_cannot_traverse() {
        assert!(valid_id("82ec306e-9781-43e2-b134-08d8e55ecb35"));
        assert!(!valid_id("../../v1/devices"));
        assert!(!valid_id("a/b"));
        assert!(!valid_id(""));
    }
}
