//! Suite de integración: port de `server/tests/test_api.py` y `test_linker.py`
//! contra el server Rust completo (CRDT embebido, superficie y-sweet).
//!
//! Un único test pesado: comparte servidor (DATA_DIR/ADMIN_TOKEN) sin carreras de env vars.

use std::sync::Arc;

use futures_util::{SinkExt, StreamExt};
use reqwest::{multipart, Client};
use serde_json::Value;
use tg_server::{auth, config, db, state};

fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(data);
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

async fn spawn() -> (Arc<Client>, String, tempfile::TempDir) {
    let dir = tempfile::tempdir().expect("tmpdir");
    std::env::set_var("DATA_DIR", dir.path());
    std::env::set_var("ADMIN_TOKEN", "test-admin");
    let settings = config::Settings::from_env().expect("settings");
    let connection = db::open(&settings.db_path).expect("db");
    let st = state::AppState::new(settings, connection);
    let app = tg_server::app(st);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    (Arc::new(Client::new()), format!("http://{addr}"), dir)
}

fn auth(h: &str) -> String {
    format!("Bearer {h}")
}

#[tokio::test]
async fn full_api_suite() {
    let (client, base, dir) = spawn().await;
    let cfg = config::Settings::from_env().expect("settings");

    // ---- health ----
    let resp = client.get(format!("{base}/health")).send().await.unwrap();
    assert_eq!(resp.status(), 200);
    assert!(resp.json::<Value>().await.unwrap()["ok"].as_bool().unwrap());

    // ---- invites: admin obligatorio ----
    let resp = client
        .post(format!("{base}/invites"))
        .json(&serde_json::json!({}))
        .header("Authorization", auth("wrong"))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401, "invite sin admin");

    let invite: Value = client
        .post(format!("{base}/invites"))
        .json(&serde_json::json!({ "days_valid": 1 }))
        .header("Authorization", auth("test-admin"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let code = invite["code"].as_str().unwrap().to_string();
    assert_eq!(invite["used_by"], Value::Null);
    assert!(invite["expires_at"].is_string());

    // ---- redeem ----
    let alice: Value = client
        .post(format!("{base}/auth/redeem"))
        .json(&serde_json::json!({ "code": code, "name": "alice" }))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let alice_token = alice["token"].as_str().unwrap().to_string();
    let alice_id = alice["user_id"].as_str().unwrap().to_string();

    // reusar la misma invitación -> 409
    let resp = client
        .post(format!("{base}/auth/redeem"))
        .json(&serde_json::json!({ "code": code, "name": "mallory" }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 409);

    // nombre repetido -> 409
    let invite2: Value = client
        .post(format!("{base}/invites"))
        .json(&serde_json::json!({}))
        .header("Authorization", auth("test-admin"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let resp = client
        .post(format!("{base}/auth/redeem"))
        .json(&serde_json::json!({ "code": invite2["code"], "name": "alice" }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 409, "nombre duplicado");

    let invite3: Value = client
        .post(format!("{base}/invites"))
        .json(&serde_json::json!({}))
        .header("Authorization", auth("test-admin"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let bob: Value = client
        .post(format!("{base}/auth/redeem"))
        .json(&serde_json::json!({ "code": invite3["code"], "name": "bob" }))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(bob["token"].as_str().unwrap().len() >= 20);
    assert_ne!(bob["user_id"], alice_id);

    // ---- me ----
    let resp = client.get(format!("{base}/me")).send().await.unwrap();
    assert_eq!(resp.status(), 401, "me sin token");
    let resp = client
        .get(format!("{base}/me"))
        .header("Authorization", auth("bogus"))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401, "me token invalido");
    let me: Value = client
        .get(format!("{base}/me"))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(me["name"], "alice");
    assert_eq!(me["id"], alice_id);

    // ---- attachments: roundtrip + dedupe ----
    let payload = b"hola mundo";
    let expected_sha = sha256_hex(payload);
    async fn upload(client: &Client, base: &str, token: &str, bytes: &[u8]) -> reqwest::Response {
        let form = multipart::Form::new().part("file", multipart::Part::bytes(bytes.to_vec()));
        client
            .post(format!("{base}/attachments"))
            .header("Authorization", format!("Bearer {token}"))
            .multipart(form)
            .send()
            .await
            .unwrap()
    }
    let r1: Value = upload(&client, &base, &alice_token, payload)
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(r1["sha256"], expected_sha);
    assert_eq!(r1["size"], 10);
    assert_eq!(r1["uri"], format!("attachment://{expected_sha}"));

    let r2: Value = upload(&client, &base, &alice_token, payload)
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(r2["sha256"], expected_sha, "dedupe por hash");

    let dl = client
        .get(format!("{base}/attachments/{expected_sha}"))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap();
    assert_eq!(dl.status(), 200);
    assert_eq!(dl.bytes().await.unwrap().as_ref(), payload);

    let resp = client
        .get(format!("{base}/attachments/zzz"))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 400, "hash invalido");

    let miss = "a".repeat(64);
    let resp = client
        .get(format!("{base}/attachments/{miss}"))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 404, "adjunto inexistente");

    // ---- docs: create/list/token ----
    let doc: Value = client
        .post(format!("{base}/docs"))
        .json(&serde_json::json!({ "title": "Reunión" }))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let doc_id = doc["id"].as_str().unwrap().to_string();

    let list: Value = client
        .get(format!("{base}/docs"))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(list.as_array().unwrap().iter().any(|d| d["id"] == doc_id));

    let token_body: Value = client
        .post(format!("{base}/docs/{doc_id}/token"))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(token_body["docId"], doc_id);
    assert!(token_body["baseUrl"].as_str().unwrap().starts_with("http"));
    assert!(token_body["url"].is_string());
    let doc_tok = token_body["token"].as_str().unwrap().to_string();
    assert_eq!(doc_tok, auth::doc_token(&doc_id, &cfg));

    // /d/* con el token de doc
    let up: Vec<u8> = client
        .get(format!("{base}/d/{doc_id}/as-update"))
        .header("Authorization", auth(&doc_tok))
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .bytes()
        .await
        .unwrap()
        .to_vec();
    assert!(!up.is_empty(), "as-update no vacío");

    let resp = client
        .get(format!("{base}/d/{doc_id}/as-update"))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401, "as-update sin token");
    let resp = client
        .get(format!("{base}/d/{doc_id}/as-update"))
        .header("Authorization", auth("bogus"))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401, "as-update token invalido");

    // apply update de vuelta (POST /d/{id}/update)
    let resp = client
        .post(format!("{base}/d/{doc_id}/update"))
        .header("Authorization", auth(&doc_tok))
        .header("content-type", "application/octet-stream")
        .body(up.clone())
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200, "update aplicado");

    // y-sweet compat: /doc/new y /doc/{id}/auth
    let newdoc: Value = client
        .post(format!("{base}/doc/new"))
        .header("Authorization", auth("test-admin"))
        .json(&serde_json::json!({}))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let ys_id = newdoc["docId"].as_str().unwrap().to_string();
    let ys_tok: Value = client
        .post(format!("{base}/doc/{ys_id}/auth"))
        .header("Authorization", auth("test-admin"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(ys_tok["docId"], ys_id);
    let resp = client
        .get(format!("{base}/d/{ys_id}/as-update"))
        .header("Authorization", auth(ys_tok["token"].as_str().unwrap()))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200, "token de /doc/auth valido");

    // ---- aliases CRUD ----
    let r: Value = client
        .post(format!("{base}/linker/{doc_id}/aliases"))
        .json(&serde_json::json!({ "doc_id": doc_id, "alias": "Los Cazafantasmas" }))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(r["alias"], "Los Cazafantasmas");

    let resp = client
        .post(format!("{base}/linker/{doc_id}/aliases"))
        .json(&serde_json::json!({ "doc_id": doc_id, "alias": "Los Cazafantasmas" }))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 409, "alias duplicado");

    let list: Value = client
        .get(format!("{base}/linker/{doc_id}/aliases"))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(list["aliases"]
        .as_array()
        .unwrap()
        .iter()
        .any(|a| a["alias"] == "Los Cazafantasmas"));

    let resp = client
        .request(
            reqwest::Method::DELETE,
            format!("{base}/linker/{doc_id}/aliases"),
        )
        .json(&serde_json::json!({ "doc_id": doc_id, "alias": "Los Cazafantasmas" }))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    let list: Value = client
        .get(format!("{base}/linker/{doc_id}/aliases"))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(list["aliases"].as_array().unwrap().is_empty());

    // ---- keywords (mirror ya escrito como espejo del doc) ----
    let kw_doc: Value = client
        .post(format!("{base}/docs"))
        .json(&serde_json::json!({ "title": "Reunion del proyecto" }))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let kw_id = kw_doc["id"].as_str().unwrap().to_string();
    std::fs::write(
        dir.path().join("mirror").join(format!("{kw_id}.md")),
        "Discutimos el deploy del servidor, el deploy y la migración de datos.",
    )
    .unwrap();
    let kws: Value = client
        .get(format!("{base}/linker/{kw_id}/keywords?top_n=5"))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let kw_list: Vec<String> = kws["keywords"]
        .as_array()
        .unwrap()
        .iter()
        .map(|k| k.as_str().unwrap().to_string())
        .collect();
    assert!(
        kw_list.iter().any(|w| w == "deploy"),
        "keywords: {kw_list:?}"
    );

    // ---- linker run target=mirror (auto-links) ----
    let geralt: Value = client
        .post(format!("{base}/docs"))
        .json(&serde_json::json!({ "title": "Geralt de Rivia" }))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let ciri: Value = client
        .post(format!("{base}/docs"))
        .json(&serde_json::json!({ "title": "Ciri" }))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let sesion: Value = client
        .post(format!("{base}/docs"))
        .json(&serde_json::json!({ "title": "Sesión 5" }))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let geralt_id = geralt["id"].as_str().unwrap().to_string();
    let resp = client
        .post(format!("{base}/linker/{geralt_id}/aliases"))
        .json(&serde_json::json!({ "doc_id": geralt_id, "alias": "Geralt" }))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200, "alias Geralt añadido");

    std::fs::write(
        dir.path()
            .join("mirror")
            .join(format!("{}.md", sesion["id"].as_str().unwrap())),
        "Geralt llego a la posada, Geralt hablo con Ciri.",
    )
    .unwrap();
    std::fs::write(
        dir.path()
            .join("mirror")
            .join(format!("{}.md", ciri["id"].as_str().unwrap())),
        "Ciri esperaba a su padre.",
    )
    .unwrap();

    let run: Value = client
        .post(format!("{base}/linker/run?target=mirror"))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(run["notes"].as_u64().unwrap() >= 5);
    let mut applied_any = false;
    for a in run["auto"].as_array().unwrap() {
        if a["doc_id"] == sesion["id"] {
            applied_any = true;
            let links: Vec<&str> = a["links"]
                .as_array()
                .unwrap()
                .iter()
                .map(|l| l.as_str().unwrap())
                .collect();
            assert!(links.contains(&"[[Geralt de Rivia]]"), "{links:?}");
            assert!(links.contains(&"[[Ciri]]"), "{links:?}");
        }
    }
    assert!(applied_any, "sesión debió tener auto-links");
    let mirrored = std::fs::read_to_string(
        dir.path()
            .join("mirror")
            .join(format!("{}.md", sesion["id"].as_str().unwrap())),
    )
    .unwrap();
    assert!(mirrored.contains("[[Geralt de Rivia]]"));
    assert!(mirrored.contains("[[Ciri]]"));

    // graph devuelve edges con los wikilinks
    let graph: Value = client
        .get(format!("{base}/linker/graph"))
        .header("Authorization", auth(&alice_token))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(graph["nodes"].as_array().unwrap().len() >= 5);
    assert!(graph["edges"].as_array().unwrap().len() >= 2);

    // ---- ws: handshake y rechazo ----
    let ws_tok = auth::doc_token(&doc_id, &cfg);
    let ws_url = format!(
        "{base_ws}/d/{doc_id}/ws/goblin-e2e?token={ws_tok}",
        base_ws = ws_base_of(&base)
    );
    let (mut socket, _r) = tokio_tungstenite::connect_async(&ws_url)
        .await
        .expect("ws connect");
    let step1 = vec![0u8, 0, 0];
    let framed = [step1.len() as u8]
        .into_iter()
        .chain(step1)
        .collect::<Vec<_>>();
    socket
        .send(tokio_tungstenite::tungstenite::Message::Binary(framed))
        .await
        .ok();
    let first = tokio::time::timeout(std::time::Duration::from_secs(6), socket.next())
        .await
        .expect("timeout esperando primer mensaje")
        .expect("primer mensaje")
        .expect("msg ok");
    let data = first.into_data();
    let bytes = strip_varint_prefix(&data);
    assert!(
        (0..=3).contains(&bytes[0]),
        "primer byte debe ser mensaje yjs (0=Sync/1=Awareness), got {}",
        bytes[0]
    );
    socket.close(None).await.ok();

    // token incorrecto -> rechazo del handshake WS con 401
    let bad_url = format!(
        "{base_ws}/d/{doc_id}/ws/goblin-e2e?token=bad",
        base_ws = ws_base_of(&base)
    );
    let res = tokio_tungstenite::connect_async(&bad_url).await;
    let bad_status = match &res {
        Err(tokio_tungstenite::tungstenite::Error::Http(resp)) => Some(resp.status()),
        _ => None,
    };
    assert_eq!(
        bad_status,
        Some(reqwest::StatusCode::UNAUTHORIZED),
        "ws token invalido, err={res:?}"
    );
}

fn ws_base_of(base: &str) -> String {
    base.replace("http://", "ws://")
}

fn strip_varint_prefix(data: &[u8]) -> &[u8] {
    let mut i = 0;
    while i < data.len() && data[i] & 0x80 != 0 {
        i += 1;
    }
    &data[(i + 1).min(data.len())..]
}
