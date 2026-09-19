use std::collections::HashMap;
use std::collections::HashSet;

use crate::normalize::{normalize_word, tokenize_with_positions};

/// Stopwords españolas ya normalizadas (minúsculas, sin acentos): el filtro se
/// aplica sobre `normalize_word`, así que el set debe estar sin acentos.
pub const STOPWORDS: &[&str] = &[
    "de",
    "la",
    "que",
    "el",
    "en",
    "y",
    "a",
    "los",
    "del",
    "se",
    "las",
    "por",
    "un",
    "para",
    "con",
    "no",
    "una",
    "su",
    "al",
    "lo",
    "como",
    "mas",
    "pero",
    "sus",
    "le",
    "ya",
    "o",
    "este",
    "si",
    "porque",
    "esta",
    "entre",
    "cuando",
    "muy",
    "sin",
    "sobre",
    "tambien",
    "me",
    "hasta",
    "hay",
    "donde",
    "quien",
    "desde",
    "todo",
    "nos",
    "durante",
    "todos",
    "uno",
    "les",
    "ni",
    "contra",
    "otros",
    "ese",
    "eso",
    "ante",
    "ellos",
    "e",
    "esto",
    "mi",
    "antes",
    "algunos",
    "que",
    "unos",
    "yo",
    "otro",
    "otras",
    "otra",
    "el",
    "tanto",
    "esa",
    "estos",
    "mucho",
    "quienes",
    "nada",
    "muchos",
    "cual",
    "poco",
    "ella",
    "estar",
    "estas",
    "algunas",
    "algo",
    "nosotros",
    "mis",
    "tu",
    "te",
    "ti",
    "tus",
    "ellas",
    "nosotras",
    "vosotros",
    "vosotras",
    "os",
    "mio",
    "mia",
    "mios",
    "mias",
    "tuyo",
    "tuya",
    "tuyos",
    "tuyas",
    "suyo",
    "suya",
    "suyos",
    "suyas",
    "nuestro",
    "nuestra",
    "nuestros",
    "nuestras",
    "vuestro",
    "vuestra",
    "vuestros",
    "vuestras",
    "esos",
    "esas",
    "aquellos",
    "aquellas",
    "aqui",
    "ahi",
    "alli",
    "como",
    "cuanto",
    "cuanta",
    "cuantos",
    "cuantas",
    "u",
    "sea",
    "ser",
    "fue",
    "era",
    "son",
    "es",
    "esta",
    "estan",
    "fui",
    "eras",
    "eres",
    "vino",
    "viene",
    "propuesta",
    "propuestas",
];

/// Palabras normalizadas y filtradas de un texto.
pub fn words(text: &str) -> Vec<String> {
    tokenize_with_positions(text)
        .iter()
        .map(|t| normalize_word(t.lit))
        .filter(|w| {
            !w.is_empty()
                && w.len() > 2
                && !STOPWORDS.contains(&w.as_str())
                && !w.chars().all(|c| c.is_ascii_digit())
        })
        .collect()
}

/// `extract_keywords` modo frecuencia (paridad con el fallback del Python).
pub fn keywords_frequency(text: &str, top_n: usize) -> Vec<String> {
    let mut counts: HashMap<String, u32> = HashMap::new();
    for w in words(text) {
        *counts.entry(w).or_default() += 1;
    }
    let mut items: Vec<(String, u32)> = Vec::from_iter(counts);
    items.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    items.into_iter().take(top_n).map(|(w, _)| w).collect()
}

/// Token normalizado que aparece en líneas de encabezado ('#').
fn heading_set(text: &str) -> HashSet<String> {
    let mut heads = HashSet::new();
    for line in text.lines() {
        let trimmed = line.trim_start();
        if let Some(rest) = trimmed.strip_prefix('#') {
            for word in words(rest) {
                heads.insert(word);
            }
        }
    }
    heads
}

/// Gramos 1 y 2 del doc con sus frecuencias.
fn grams_with_counts(doc: &[String]) -> HashMap<String, u32> {
    let mut grams: HashMap<String, u32> = HashMap::new();
    for (i, w) in doc.iter().enumerate() {
        *grams.entry(w.clone()).or_default() += 1;
        if i + 1 < doc.len() {
            let bigram = format!("{w} {}", doc[i + 1]);
            *grams.entry(bigram).or_default() += 1;
        }
    }
    grams
}

/// `extract_keywords` determinista (decisión de arquitectura: TF-IDF sin ML, sin keyBERT).
///
/// IDF con suavizado `ln((1+N)/(1+df)) + 1`; bonus x2 si el gramo (o parte de él)
/// aparece en un encabezado '#'.
pub fn keywords_tfidf(text: &str, corpus: &[&str], top_n: usize) -> Vec<String> {
    let doc_words = words(text);
    if doc_words.is_empty() {
        return Vec::new();
    }
    let heads = heading_set(text);
    let text_grams = grams_with_counts(&doc_words);

    // frecuencia de documento sobre el corpus + el propio doc (TF-IDF estándar)
    let mut df: HashMap<String, usize> = HashMap::new();
    let n_docs = corpus.len() + 1;
    fn tally(df: &mut HashMap<String, usize>, keys: HashSet<String>) {
        for g in keys {
            *df.entry(g).or_default() += 1;
        }
    }
    let all_keys: HashSet<String> = text_grams.keys().cloned().collect();
    tally(&mut df, all_keys);
    for doc in corpus {
        if *doc == text {
            continue;
        }
        let grams = grams_with_counts(&words(doc));
        if grams.is_empty() {
            continue;
        }
        let doc_keys: HashSet<String> = grams.keys().cloned().collect();
        tally(&mut df, doc_keys);
    }

    let mut scored: Vec<(String, f64)> = Vec::new();
    for (gram, tf) in &text_grams {
        let df = df.get(gram).copied().unwrap_or(1).max(1);
        let idf = ((1.0 + n_docs as f64) / (1.0 + df as f64)).ln() + 1.0;
        let mut score = (*tf as f64) * idf;
        let bonus = gram.split(' ').any(|w| heads.contains(w));
        if bonus {
            score *= 2.0;
        }
        scored.push((gram.clone(), score));
    }

    scored.sort_by(|a, b| {
        b.1.partial_cmp(&a.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.0.cmp(&b.0))
    });
    scored.into_iter().take(top_n).map(|(g, _)| g).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_frequency_keywords() {
        let text = "Discutimos el deploy del servidor. El deploy y la migración del deploy.";
        let kw = keywords_frequency(text, 5);
        assert_eq!(kw[0], "deploy");
        assert!(kw.iter().any(|w| w == "migracion"));
    }

    #[test]
    fn test_tfidf_prefers_distinctive() {
        let text = "El deploy usando Docker. El deploy y Docker para el deploy.";
        let corpus = ["Docker y el deploy", "deploy", "El deploy otra vez deploy"];
        let kw = keywords_tfidf(text, &corpus, 5);
        // "deploy" aparece en todo el corpus -> IDF bajo; "docker" distingue
        assert_eq!(kw[0], "docker");
    }

    #[test]
    fn test_heading_bonus() {
        let text = "# Deploy\n\nHablamos del deploy y de la migración.";
        let kw = keywords_tfidf(text, &[text], 5);
        assert_eq!(kw[0], "deploy", "encabezado debe dominar");
    }
}
