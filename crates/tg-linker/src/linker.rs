use std::collections::HashMap;
use std::collections::HashSet;

use serde::Serialize;

use crate::normalize::{normalize, normalize_word, tokenize_with_positions, Token};

/// Nota fuente (lo que cargaría el servidor desde SQLite).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NoteSpec {
    pub id: String,
    pub title: String,
    pub aliases: Vec<String>,
}

/// Edición sobre el contenido del doc (offsets de byte, no de char).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LinkEdit {
    pub start: usize,
    pub end: usize,
    pub replacement: String,
    pub target_id: String,
}

/// Auto-link aplicado (el texto exacto pisado y el edit a aplicar).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct AutoLink {
    pub target_id: String,
    pub edit: LinkEdit,
}

/// Propuesta pendiente de aprobación (schema `proposals`, igual que el Python).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Proposal {
    pub doc_id: String,
    pub target_id: String,
    pub literal: String,
    pub confidence: f64,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct DocLinkResult {
    pub auto: Vec<AutoLink>,
    pub proposals: Vec<Proposal>,
}

struct NoteData {
    id: String,
    title: String,
    variants: Vec<String>,
    exact_variants: HashSet<String>,
}

/// Igual que `expand_variants` del Python: título + alias + prefijos de N palabras.
pub fn expand_variants(title: &str, aliases: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    for name in std::iter::once(title).chain(aliases.iter().map(|a| a.as_str())) {
        let words = tokenize_with_positions(name)
            .iter()
            .map(|t| normalize_word(t.lit))
            .collect::<Vec<_>>();
        for i in 1..=words.len() {
            let candidate = words[..i].join(" ");
            if seen.insert(candidate.clone()) {
                out.push(candidate);
            }
        }
    }
    out
}

/// Busca la secuencia de tokens del variant en los tokens ya tokenizados del contenido.
/// Devuelve (literal coincidente, start byte, end byte).
fn match_variant_in_tokens<'a>(
    tokens: &[Token<'a>],
    keys: &[String],
    variant: &str,
) -> Option<(String, usize, usize)> {
    let parts: Vec<&str> = variant.split(' ').collect();
    if parts.is_empty() || parts.len() > keys.len() {
        return None;
    }
    'outer: for win in 0..=keys.len() - parts.len() {
        for (k, p) in parts.iter().enumerate() {
            if keys[win + k] != *p {
                continue 'outer;
            }
        }
        let start = tokens[win].start;
        let end = tokens[win + parts.len() - 1].end;
        let lit = tokens[win..win + parts.len()]
            .iter()
            .map(|t| t.lit)
            .collect::<Vec<_>>()
            .join(" ");
        return Some((lit, start, end));
    }
    None
}

/// Núcleo de `_link_doc`: dado el contenido de un doc y el set de notas, genera
/// auto-links (confianza 1.0) y propuestas pendientes.
pub fn link_doc(content: &str, note_id: &str, notes: &[NoteSpec]) -> DocLinkResult {
    let mut notes_data: Vec<NoteData> = notes
        .iter()
        .map(|n| {
            let mut exact_variants = HashSet::new();
            exact_variants.insert(normalize(&n.title));
            for a in &n.aliases {
                exact_variants.insert(normalize(a));
            }
            NoteData {
                id: n.id.clone(),
                title: n.title.clone(),
                variants: expand_variants(&n.title, &n.aliases),
                exact_variants,
            }
        })
        .collect();
    notes_data.sort_by(|a, b| a.id.cmp(&b.id));

    let mut variant_owner: HashMap<String, Vec<usize>> = HashMap::new();
    for (idx, nd) in notes_data.iter().enumerate() {
        for v in &nd.variants {
            variant_owner.entry(v.clone()).or_default().push(idx);
        }
    }

    let tokens = tokenize_with_positions(content);
    let keys: Vec<String> = tokens.iter().map(|t| normalize_word(t.lit)).collect();

    let mut used: Vec<(usize, usize)> = Vec::new();
    let mut auto = Vec::new();
    let mut proposals = Vec::new();

    for other in notes_data.iter() {
        if other.id == note_id {
            continue;
        }
        let wikilink = format!("[[{}]]", other.title);
        let already = content.contains(&wikilink);
        for variant in &other.variants {
            if already {
                break;
            }
            let Some((literal, start, end)) = match_variant_in_tokens(&tokens, &keys, variant)
            else {
                continue;
            };
            if used.iter().any(|(s, e)| start < *e && *s < end) {
                continue;
            }
            let owners = variant_owner.get(variant).map_or(0, |v| v.len());
            let exact = other.exact_variants.contains(variant);
            let unambiguous = owners == 1;
            let confidence = if exact && unambiguous {
                1.0
            } else if exact {
                0.6
            } else {
                0.4
            };

            if confidence >= 0.999 {
                let edit = LinkEdit {
                    start,
                    end,
                    replacement: wikilink.clone(),
                    target_id: other.id.clone(),
                };
                auto.push(AutoLink {
                    target_id: other.id.clone(),
                    edit: edit.clone(),
                });
                used.push((start, end));
            } else {
                proposals.push(Proposal {
                    doc_id: note_id.to_string(),
                    target_id: other.id.clone(),
                    literal,
                    confidence,
                });
            }
        }
    }

    auto.sort_by_key(|a| a.edit.start);
    DocLinkResult { auto, proposals }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn n(id: &str, title: &str, aliases: &[&str]) -> NoteSpec {
        NoteSpec {
            id: id.into(),
            title: title.into(),
            aliases: aliases.iter().map(|s| s.to_string()).collect(),
        }
    }

    #[test]
    fn test_expand_variants_prefixes() {
        let v = expand_variants("Geralt de Rivia", &[]);
        assert_eq!(v, vec!["geralt", "geralt de", "geralt de rivia"]);
    }

    #[test]
    fn test_auto_links_unambiguous_content() {
        // caso equivalente a test_link_auto_ambiguous_and_proposal del Python
        let notes = vec![
            n("doc_Geralt de Rivia", "Geralt de Rivia", &["Geralt"]),
            n("doc_Ciri", "Ciri", &[]),
            n("doc_Yennefer", "Yennefer", &[]),
            n("doc_Sesion 5", "Sesión 5", &[]),
        ];
        let content = "Geralt llego a la posada, Geralt hablo con Ciri.";
        let res = link_doc(content, "doc_Sesion 5", &notes);

        assert_eq!(
            res.auto
                .iter()
                .map(|a| (a.target_id.clone(), a.edit.start))
                .collect::<Vec<_>>(),
            vec![
                ("doc_Geralt de Rivia".to_string(), 0),
                ("doc_Ciri".to_string(), 43)
            ]
        );
        assert_eq!(res.auto[0].edit.replacement, "[[Geralt de Rivia]]");
        assert_eq!(res.auto[1].edit.replacement, "[[Ciri]]");
        assert!(res.proposals.is_empty(), "no debería haber propuestas");
    }

    #[test]
    fn test_overlap_keeps_first_target() {
        // "Ci" (alias de Cirio) es prefijo del título "Ciriano": el span 0.. queda
        // ocupado por Ciriano (id ordena primero) y la variante de Cirio no aplica.
        let notes = vec![
            n("doc_Cirio", "Cirio", &["Ci"]),
            n("doc_Ciriano", "Ciriano", &[]),
            n("doc_Doc", "Doc", &[]),
        ];
        let content = "Ciriano y el resto.";
        let res = link_doc(content, "doc_Doc", &notes);
        assert_eq!(
            res.auto.len(),
            1,
            "solo un auto-link (Ciriano, span completo)"
        );
        assert_eq!(res.auto[0].target_id, "doc_Ciriano");
        assert!(
            res.proposals.is_empty(),
            "el span ya está usado -> ni propuesta"
        );
    }

    #[test]
    fn test_ambiguous_goes_to_proposals() {
        let notes = vec![
            n("doc_A", "Notas", &[]),
            n("doc_B", "Notas", &[]),
            n("doc_C", "Doc", &[]),
        ];
        let content = "Escribimos las Notas.";
        let res = link_doc(content, "doc_C", &notes);
        assert!(res.auto.is_empty(), "ambiguo -> sin auto");
        // cada nota que reclama el span no-ocupado genera su propuesta (paridad con el Python)
        assert_eq!(res.proposals.len(), 2);
        for p in &res.proposals {
            assert_eq!(p.confidence, 0.6); // exacto pero ambiguo
        }
    }

    #[test]
    fn test_skips_already_linked() {
        let notes = vec![n("doc_Ciri", "Ciri", &[]), n("doc_Doc", "Doc", &[])];
        let content = "[[Ciri]] ya está vinculado y Ciri otra vez.";
        let res = link_doc(content, "doc_Doc", &notes);
        assert!(res.auto.is_empty(), "ya hay un [[Ciri]] -> sin auto-links");
    }
}
