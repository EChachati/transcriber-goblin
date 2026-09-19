use unicode_normalization::UnicodeNormalization;

/// Token con offsets de bytes sobre el texto original.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Token<'a> {
    pub lit: &'a str,
    pub start: usize,
    pub end: usize,
}

/// ¿Este char es parte de una palabra? (análogo a `\w` + el rango `Á-ú` del Python).
fn is_word_char(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Tokeniza el texto en palabras (unicode) con posición byte a byte.
pub fn tokenize_with_positions(text: &str) -> Vec<Token<'_>> {
    let mut out = Vec::new();
    let mut start: Option<usize> = None;
    for (idx, ch) in text.char_indices() {
        if is_word_char(ch) {
            if start.is_none() {
                start = Some(idx);
            }
        } else if let Some(s) = start.take() {
            out.push(Token {
                lit: &text[s..idx],
                start: s,
                end: idx,
            });
        }
    }
    if let Some(s) = start.take() {
        out.push(Token {
            lit: &text[s..],
            start: s,
            end: text.len(),
        });
    }
    out
}

/// Normaliza una palabra: minúsculas, sin acentos (NFKD), y sin 's'/'es' final de plural.
pub fn normalize_word(word: &str) -> String {
    let base: String = word
        .to_lowercase()
        .nfkd()
        .filter(|c| !unicode_normalization::char::is_combining_mark(*c))
        .collect();
    match strip_plural(&base) {
        Some(stripped) => stripped,
        None => base,
    }
}

/// Réplica del regex `^(?P<word>.+?)(?P<s>s|es)$` con base > 2 chars.
fn strip_plural(word: &str) -> Option<String> {
    let chars: Vec<char> = word.chars().collect();
    let n = chars.len();
    if n < 3 {
        return None;
    }
    // intentar "es" primero (equivalente a que `.+?` sea lo más corto posible; el
    // resultado simplificado coincide con el comportamiento del regex para palabras reales)
    if n >= 4 && chars[n - 2] == 'e' && chars[n - 1] == 's' {
        let base_len = n - 2;
        if base_len > 2 {
            return Some(chars[..base_len].iter().collect());
        }
    }
    if chars[n - 1] == 's' {
        let base_len = n - 1;
        if base_len > 2 {
            return Some(chars[..base_len].iter().collect());
        }
    }
    None
}

/// Minúsculas, sin acentos/tildes, y con la 's' final de plural quitada (normalize() del Python).
pub fn normalize(text: &str) -> String {
    tokenize_with_positions(text)
        .iter()
        .map(|t| normalize_word(t.lit))
        .collect::<Vec<_>>()
        .join(" ")
}
