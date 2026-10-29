//! Minimal strict JSON parser that keeps byte spans for every value and key.
//! Used to locate the exact source bytes of one FD object so only those bytes are disclosed.
//! Fails closed: any syntax error, trailing garbage or invalid escape is an error.

#[derive(Debug, Clone)]
pub enum JV {
    Obj(Vec<(String, (usize, usize), JNode)>), // key, key span (incl. quotes), value
    Arr(Vec<JNode>),
    Str(String),
    Num(String),
    Bool(bool),
    Null,
}

#[derive(Debug, Clone)]
pub struct JNode {
    pub start: usize,
    pub end: usize, // exclusive
    pub v: JV,
}

pub fn parse(buf: &[u8]) -> Result<JNode, String> {
    let mut p = P { b: buf, i: 0, depth: 0 };
    p.ws();
    let n = p.value()?;
    p.ws();
    if p.i != buf.len() {
        return Err(format!("trailing bytes at {}", p.i));
    }
    Ok(n)
}

struct P<'a> {
    b: &'a [u8],
    i: usize,
    depth: usize,
}

impl P<'_> {
    fn ws(&mut self) {
        while self.i < self.b.len() && matches!(self.b[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }
    fn peek(&self) -> Option<u8> {
        self.b.get(self.i).copied()
    }
    fn expect(&mut self, c: u8) -> Result<(), String> {
        if self.peek() == Some(c) {
            self.i += 1;
            Ok(())
        } else {
            Err(format!("expected '{}' at {}", c as char, self.i))
        }
    }
    fn value(&mut self) -> Result<JNode, String> {
        self.depth += 1;
        if self.depth > 64 {
            return Err("nesting too deep".into());
        }
        let start = self.i;
        let v = match self.peek().ok_or("unexpected end")? {
            b'{' => self.object()?,
            b'[' => self.array()?,
            b'"' => JV::Str(self.string()?),
            b't' => self.lit(b"true", JV::Bool(true))?,
            b'f' => self.lit(b"false", JV::Bool(false))?,
            b'n' => self.lit(b"null", JV::Null)?,
            b'-' | b'0'..=b'9' => self.number()?,
            c => return Err(format!("unexpected '{}' at {}", c as char, self.i)),
        };
        self.depth -= 1;
        Ok(JNode { start, end: self.i, v })
    }
    fn lit(&mut self, s: &[u8], v: JV) -> Result<JV, String> {
        if self.b[self.i..].starts_with(s) {
            self.i += s.len();
            Ok(v)
        } else {
            Err(format!("bad literal at {}", self.i))
        }
    }
    fn number(&mut self) -> Result<JV, String> {
        let s = self.i;
        if self.peek() == Some(b'-') {
            self.i += 1;
        }
        let d0 = self.i;
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            self.i += 1;
        }
        if self.i == d0 {
            return Err(format!("bad number at {s}"));
        }
        if self.peek() == Some(b'.') {
            self.i += 1;
            let d1 = self.i;
            while matches!(self.peek(), Some(b'0'..=b'9')) {
                self.i += 1;
            }
            if self.i == d1 {
                return Err(format!("bad number at {s}"));
            }
        }
        if matches!(self.peek(), Some(b'e' | b'E')) {
            self.i += 1;
            if matches!(self.peek(), Some(b'+' | b'-')) {
                self.i += 1;
            }
            let d2 = self.i;
            while matches!(self.peek(), Some(b'0'..=b'9')) {
                self.i += 1;
            }
            if self.i == d2 {
                return Err(format!("bad number at {s}"));
            }
        }
        Ok(JV::Num(String::from_utf8_lossy(&self.b[s..self.i]).into()))
    }
    fn hex4(&mut self) -> Result<u32, String> {
        let h = self.b.get(self.i..self.i + 4).ok_or("short \\u escape")?;
        let s = std::str::from_utf8(h).map_err(|_| "bad \\u escape")?;
        let v = u32::from_str_radix(s, 16).map_err(|_| "bad \\u escape")?;
        self.i += 4;
        Ok(v)
    }
    fn string(&mut self) -> Result<String, String> {
        self.expect(b'"')?;
        let mut out: Vec<u8> = vec![];
        loop {
            let c = self.peek().ok_or("unterminated string")?;
            self.i += 1;
            match c {
                b'"' => break,
                b'\\' => {
                    let e = self.peek().ok_or("bad escape")?;
                    self.i += 1;
                    match e {
                        b'"' => out.push(b'"'),
                        b'\\' => out.push(b'\\'),
                        b'/' => out.push(b'/'),
                        b'b' => out.push(8),
                        b'f' => out.push(12),
                        b'n' => out.push(b'\n'),
                        b'r' => out.push(b'\r'),
                        b't' => out.push(b'\t'),
                        b'u' => {
                            let mut cp = self.hex4()?;
                            if (0xD800..0xDC00).contains(&cp) {
                                if self.b.get(self.i..self.i + 2) != Some(b"\\u") {
                                    return Err("lone surrogate".into());
                                }
                                self.i += 2;
                                let lo = self.hex4()?;
                                if !(0xDC00..0xE000).contains(&lo) {
                                    return Err("bad surrogate pair".into());
                                }
                                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                            } else if (0xDC00..0xE000).contains(&cp) {
                                return Err("lone surrogate".into());
                            }
                            let ch = char::from_u32(cp).ok_or("bad code point")?;
                            let mut tmp = [0u8; 4];
                            out.extend_from_slice(ch.encode_utf8(&mut tmp).as_bytes());
                        }
                        _ => return Err(format!("bad escape at {}", self.i)),
                    }
                }
                0..=0x1f => return Err("control char in string".into()),
                _ => out.push(c),
            }
        }
        String::from_utf8(out).map_err(|_| "invalid utf-8".into())
    }
    fn object(&mut self) -> Result<JV, String> {
        self.expect(b'{')?;
        let mut items = vec![];
        self.ws();
        if self.peek() == Some(b'}') {
            self.i += 1;
            return Ok(JV::Obj(items));
        }
        loop {
            self.ws();
            let ks = self.i;
            let k = self.string()?;
            let ke = self.i;
            self.ws();
            self.expect(b':')?;
            self.ws();
            let v = self.value()?;
            items.push((k, (ks, ke), v));
            self.ws();
            match self.peek() {
                Some(b',') => self.i += 1,
                Some(b'}') => {
                    self.i += 1;
                    break;
                }
                _ => return Err(format!("expected ',' or '}}' at {}", self.i)),
            }
        }
        Ok(JV::Obj(items))
    }
    fn array(&mut self) -> Result<JV, String> {
        self.expect(b'[')?;
        let mut items = vec![];
        self.ws();
        if self.peek() == Some(b']') {
            self.i += 1;
            return Ok(JV::Arr(items));
        }
        loop {
            self.ws();
            items.push(self.value()?);
            self.ws();
            match self.peek() {
                Some(b',') => self.i += 1,
                Some(b']') => {
                    self.i += 1;
                    break;
                }
                _ => return Err(format!("expected ',' or ']' at {}", self.i)),
            }
        }
        Ok(JV::Arr(items))
    }
}

impl JNode {
    /// Scalar value as text (strings unescaped; numbers verbatim).
    pub fn scalar(&self) -> Option<String> {
        match &self.v {
            JV::Str(s) => Some(s.clone()),
            JV::Num(n) => Some(n.clone()),
            JV::Bool(b) => Some(b.to_string()),
            _ => None,
        }
    }
    /// Look up a key in an object. Err if the key appears more than once (ambiguous).
    pub fn get_unique(&self, key: &str) -> Result<Option<&JNode>, String> {
        let JV::Obj(items) = &self.v else { return Err("not an object".into()) };
        let hits: Vec<&JNode> = items.iter().filter(|(k, _, _)| k == key).map(|(_, _, v)| v).collect();
        match hits.len() {
            0 => Ok(None),
            1 => Ok(Some(hits[0])),
            n => Err(format!("key '{key}' appears {n} times")),
        }
    }
    /// All objects (at any depth) that contain `key` whose scalar value == `val`.
    pub fn find_objects<'a>(&'a self, key: &str, val: &str, out: &mut Vec<&'a JNode>) {
        match &self.v {
            JV::Obj(items) => {
                if items.iter().any(|(k, _, v)| k == key && v.scalar().as_deref() == Some(val)) {
                    out.push(self);
                }
                for (_, _, v) in items {
                    v.find_objects(key, val, out);
                }
            }
            JV::Arr(items) => {
                for v in items {
                    v.find_objects(key, val, out);
                }
            }
            _ => {}
        }
    }
    /// Count objects (any depth) that contain `key` at all.
    pub fn count_with_key(&self, key: &str) -> usize {
        match &self.v {
            JV::Obj(items) => {
                (items.iter().any(|(k, _, _)| k == key) as usize)
                    + items.iter().map(|(_, _, v)| v.count_with_key(key)).sum::<usize>()
            }
            JV::Arr(items) => items.iter().map(|v| v.count_with_key(key)).sum(),
            _ => 0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn whitespace_and_escapes() {
        let s = br#" { "status" : "ACTIVE" , "nominee":"A \"B\" C\\" , "n": -1.5e3 } "#;
        let n = parse(s).unwrap();
        assert_eq!(n.get_unique("status").unwrap().unwrap().scalar().unwrap(), "ACTIVE");
        assert_eq!(n.get_unique("nominee").unwrap().unwrap().scalar().unwrap(), "A \"B\" C\\");
        assert_eq!(n.get_unique("n").unwrap().unwrap().scalar().unwrap(), "-1.5e3");
        // span of the object excludes surrounding whitespace
        assert_eq!(&s[n.start..n.start + 1], b"{");
        assert_eq!(&s[n.end - 1..n.end], b"}");
    }
    #[test]
    fn duplicate_keys_are_ambiguous() {
        let n = parse(br#"{"status":"CLOSED","status":"ACTIVE"}"#).unwrap();
        assert!(n.get_unique("status").is_err());
    }
    #[test]
    fn rejects_malformed() {
        for bad in [&br#"{"a":1"#[..], br#"{"a":1,}"#, br#"{"a":"\x"}"#, br#"{"a":1} x"#, br#"{'a':1}"#, br#"{"a":01x}"#] {
            assert!(parse(bad).is_err(), "should reject {}", String::from_utf8_lossy(bad));
        }
    }
    #[test]
    fn finds_exact_object_among_many() {
        let s = br#"{"deposits":[{"depositNo":"FD-1","status":"ACTIVE"},{"depositNo":"FD-2","status":"CLOSED"}]}"#;
        let n = parse(s).unwrap();
        let mut v = vec![];
        n.find_objects("depositNo", "FD-2", &mut v);
        assert_eq!(v.len(), 1);
        let o = v[0];
        assert_eq!(o.get_unique("status").unwrap().unwrap().scalar().unwrap(), "CLOSED");
        // the object's own span does not contain the other deposit
        assert!(!String::from_utf8_lossy(&s[o.start..o.end]).contains("FD-1"));
        assert_eq!(n.count_with_key("depositNo"), 2);
    }
    #[test]
    fn substring_of_escaped_string_is_not_an_object() {
        // a customer-editable nickname containing JSON-looking text must not parse as an object
        let inner = br#"{\"depositNo\":\"FD-9\",\"status\":\"ACTIVE\"}"#;
        assert!(parse(inner).is_err());
    }
    #[test]
    fn unicode_escapes() {
        let n = parse(br#"{"a":"\u20b9 \ud83d\ude00"}"#).unwrap();
        assert_eq!(n.get_unique("a").unwrap().unwrap().scalar().unwrap(), "\u{20b9} \u{1F600}");
        assert!(parse(br#"{"a":"\ud83d"}"#).is_err());
    }
}
