//! The Che filter for videos edited in Chỉnh sửa (Issue #510): the same
//! FFmpeg graph as the engine's `cover_filtergraph` (engine/dubflow/media/
//! adapter.py), so a region looks the same whether a dubbing job or an
//! export hides it. Coordinates are fractions of the frame, kept even for
//! 4:2:0 chroma; times are seconds of the main video.

/// A rectangle to hide, with its window in ms (None: the whole video).
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Area {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
    pub window: Option<(u64, u64)>,
}

/// The style, already validated (see `CoverStyle::validate`).
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Look<'a> {
    pub mode: &'a str,
    pub direction: &'a str,
    pub strip_px: u32,
    pub color: &'a str,
    pub block: u32,
    pub feather: u32,
}

const OVERLAY_BLUR_REGIONS: usize = 8;
const COVER_CHAINS: usize = 12;
const ENABLE_TERMS: usize = 48;
const BLUR: &str = "gblur=sigma=40:steps=3";

fn even(size: &str, fraction: f64) -> String {
    format!("trunc({size}*{fraction:.6}/2)*2")
}

fn sized(size: &str, fraction: f64) -> String {
    format!("max(2\\,{})", even(size, fraction))
}

fn seconds(ms: u64) -> String {
    format!("{:.3}", ms as f64 / 1000.0)
}

/// The fragment: one unlabeled input, ending unlabeled. None without areas.
pub(crate) fn cover_filter(areas: &[Area], look: &Look) -> Option<String> {
    if areas.is_empty() {
        return None;
    }
    Some(if look.mode == "blur" { blur(areas) } else if look.mode == "color" { color(areas, look.color) } else { chains(areas, look) })
}

fn blur(areas: &[Area]) -> String {
    let enabled = |area: &Area| area.window.map_or(String::new(), |(start, end)| format!(":enable=between(t\\,{}\\,{})", seconds(start), seconds(end)));
    let count = areas.len();
    if count <= OVERLAY_BLUR_REGIONS {
        let mut parts = vec![format!("split={}[blur_b0]{}", count + 1, (0..count).map(|index| format!("[blur_r{index}]")).collect::<String>())];
        for (index, area) in areas.iter().enumerate() {
            parts.push(format!("[blur_r{index}]crop=w={}:h={}:x={}:y={},{BLUR}[blur_k{index}]",
                even("iw", area.w), even("ih", area.h), even("iw", area.x), even("ih", area.y)));
        }
        for (index, area) in areas.iter().enumerate() {
            let label = if index == count - 1 { String::new() } else { format!("[blur_b{}]", index + 1) };
            parts.push(format!("[blur_b{index}][blur_k{index}]overlay=x={}:y={}{}{label}", even("W", area.x), even("H", area.y), enabled(area)));
        }
        return parts.join(";");
    }
    let top = areas.iter().map(|area| area.y).fold(f64::INFINITY, f64::min);
    let bottom = areas.iter().map(|area| area.y + area.h).fold(0.0, f64::max).min(1.0);
    let band = format!("crop=w=iw:h=min(ih\\,{}+2)-{}:x=0:y={}", even("ih", bottom), even("ih", top), even("ih", top));
    let boxes: Vec<String> = areas.iter().map(|area| format!("drawbox=x={}:y={}:w=max(2\\,{}):h=max(2\\,{}):color=white:t=fill{}",
        even("iw", area.x), even("ih", area.y), even("iw", area.w), even("ih", area.h), enabled(area))).collect();
    [
        "split=3[blur_b0][blur_band][blur_canvas]".to_owned(),
        format!("[blur_band]{band},{BLUR}[blur_soft]"),
        format!("[blur_canvas][blur_soft]overlay=x=0:y={},split=2[blur_full][blur_m]", even("H", top)),
        format!("[blur_m]format=gray,drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill,{}[blur_mask]", boxes.join(",")),
        "[blur_full][blur_mask]alphamerge[blur_top]".to_owned(),
        "[blur_b0][blur_top]overlay".to_owned(),
    ].join(";")
}

/// Regions sharing a rectangle: `[x0, y0, x1, y1]` and coalesced windows (None: always).
#[derive(Debug, Clone, PartialEq)]
struct Group {
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
    windows: Option<Vec<(u64, u64)>>,
}

fn coalesce(windows: Option<Vec<(u64, u64)>>) -> Option<Vec<(u64, u64)>> {
    let mut windows = windows?;
    windows.sort_unstable();
    let mut merged: Vec<(u64, u64)> = Vec::new();
    for (start, end) in windows {
        match merged.last_mut() {
            Some(last) if start <= last.1 => last.1 = last.1.max(end),
            _ => merged.push((start, end)),
        }
    }
    Some(merged)
}

fn groups(areas: &[Area]) -> Vec<Group> {
    let mut result: Vec<Group> = Vec::new();
    for area in areas {
        let at = result.iter().position(|group| group.x0 == area.x && group.y0 == area.y && group.x1 == area.x + area.w && group.y1 == area.y + area.h);
        let index = at.unwrap_or_else(|| {
            result.push(Group { x0: area.x, y0: area.y, x1: area.x + area.w, y1: area.y + area.h, windows: Some(Vec::new()) });
            result.len() - 1
        });
        let group = &mut result[index];
        match (area.window, group.windows.as_mut()) {
            (None, _) => group.windows = None,
            (Some(window), Some(windows)) => windows.push(window),
            (Some(_), None) => {}
        }
    }
    for group in &mut result {
        group.windows = coalesce(group.windows.take());
    }
    result
}

fn union(a: &Group, b: &Group) -> Group {
    let windows = match (&a.windows, &b.windows) {
        (Some(first), Some(second)) => coalesce(Some([first.clone(), second.clone()].concat())),
        _ => None,
    };
    Group { x0: a.x0.min(b.x0), y0: a.y0.min(b.y0), x1: a.x1.max(b.x1), y1: a.y1.max(b.y1), windows }
}

fn merge(groups: Vec<Group>, limit: usize) -> Vec<Group> {
    let mut sorted = groups;
    sorted.sort_by(|a, b| (a.y0 + a.y1).total_cmp(&(b.y0 + b.y1)).then(a.x0.total_cmp(&b.x0)));
    let mut rows: Vec<Group> = Vec::new();
    for group in sorted {
        let height = group.y1 - group.y0;
        match rows.iter_mut().find(|row| row.y1.min(group.y1) - row.y0.max(group.y0) >= 0.5 * height.min(row.y1 - row.y0)) {
            Some(row) => *row = union(row, &group),
            None => rows.push(group),
        }
    }
    rows.sort_by(|a, b| (a.y0 + a.y1).total_cmp(&(b.y0 + b.y1)).then(a.x0.total_cmp(&b.x0)));
    let area = |group: &Group| (group.x1 - group.x0) * (group.y1 - group.y0);
    while rows.len() > limit {
        let index = (0..rows.len() - 1).min_by(|&i, &j| {
            let cost = |k: usize| area(&union(&rows[k], &rows[k + 1])) - area(&rows[k]) - area(&rows[k + 1]);
            cost(i).total_cmp(&cost(j))
        }).unwrap_or(0);
        let joined = union(&rows[index], &rows[index + 1]);
        rows.splice(index..index + 2, [joined]);
    }
    rows
}

fn enables(windows: &Option<Vec<(u64, u64)>>) -> Vec<String> {
    let Some(windows) = windows else { return vec![String::new()] };
    let terms: Vec<String> = windows.iter().map(|(start, end)| format!("between(t\\,{}\\,{})", seconds(*start), seconds(*end))).collect();
    terms.chunks(ENABLE_TERMS).map(|chunk| format!(":enable={}", chunk.join("+"))).collect()
}

fn color(areas: &[Area], color: &str) -> String {
    let color = format!("0x{}", color.trim_start_matches('#').to_uppercase());
    groups(areas).iter().flat_map(|group| enables(&group.windows).into_iter().map(move |enable| (group.clone(), enable)))
        .map(|(group, enable)| format!("drawbox=x={}:y={}:w=max(2\\,{}):h=max(2\\,{}):color={color}:t=fill{enable}",
            even("iw", group.x0), even("ih", group.y0), even("iw", group.x1 - group.x0), even("ih", group.y1 - group.y0)))
        .collect::<Vec<_>>().join(",")
}

fn strip_crop(x: f64, w: f64, y0: f64, y1: f64, strip: f64, side: &str) -> String {
    let side = if side == "top" && y0 < strip && y1 + strip <= 1.0 {
        "bottom"
    } else if side == "bottom" && y1 + strip > 1.0 && y0 >= strip {
        "top"
    } else {
        side
    };
    let height = format!("max(2\\,{})", even("ih", strip));
    let y = if side == "top" { format!("max(0\\,{}-oh)", even("ih", y0)) } else { format!("min(ih-oh\\,{})", even("ih", y1)) };
    format!("crop=w={}:h={height}:x={}:y={y}", sized("iw", w), even("iw", x))
}

fn feathered(label: &str, height: f64, feather: u32) -> Vec<String> {
    if feather == 0 {
        return Vec::new();
    }
    let inset = format!("trunc(ih*{:.6})", f64::from(feather) / (1080.0 * height));
    let sigma = f64::from(feather) / 2.0;
    vec![
        format!("[{label}]format=yuva420p,split=2[{label}a][{label}m]"),
        format!("[{label}m]format=gray,drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill,\
drawbox=x={inset}:y={inset}:w=max(2\\,iw-2*{inset}):h=max(2\\,ih-2*{inset}):color=white:t=fill,\
lut=c0=clip((val-16)*255/219\\,0\\,255),gblur=sigma={sigma}[{label}k]"),
        format!("[{label}a][{label}k]alphamerge[{label}f]"),
    ]
}

fn chains(areas: &[Area], look: &Look) -> String {
    let mut groups = groups(areas);
    if groups.len() > COVER_CHAINS {
        groups = merge(groups, COVER_CHAINS);
    }
    if look.mode == "stretch" && look.direction == "bottom" {
        groups.sort_by(|a, b| b.y1.total_cmp(&a.y1));
    } else if look.mode == "stretch" {
        groups.sort_by(|a, b| a.y0.total_cmp(&b.y0));
    }
    let strip = f64::from(look.strip_px) / 1080.0;
    let last = groups.len() - 1;
    let mut parts: Vec<String> = Vec::new();
    for (index, group) in groups.iter().enumerate() {
        let (x0, y0, y1) = (group.x0, group.y0, group.y1);
        let (w, h) = (group.x1 - group.x0, y1 - y0);
        let name = format!("cv{index}");
        let source = if index == 0 { String::new() } else { format!("[{name}]") };
        let size = format!("w={}:h={}", sized("rw", w), sized("rh", h));
        if look.mode == "mosaic" {
            let rows = ((h * 1080.0 / f64::from(look.block)).round() as i64).max(1);
            parts.push(format!("{source}split=3[{name}m][{name}s][{name}r]"));
            parts.push(format!("[{name}s]crop=w={}:h={}:x={}:y={},scale=w=max(1\\,round(iw*{rows}/ih)):h={rows}:flags=area[{name}d]",
                sized("iw", w), sized("ih", h), even("iw", x0), even("ih", y0)));
            parts.push(format!("[{name}d][{name}r]scale={size}:flags=neighbor[{name}p]"));
        } else if look.direction == "both" {
            let half = h / 2.0;
            parts.push(format!("{source}split=5[{name}m][{name}s][{name}t][{name}r][{name}q]"));
            parts.push(format!("[{name}s]{}[{name}c]", strip_crop(x0, w, y0, y1, strip, "top")));
            parts.push(format!("[{name}t]{}[{name}e]", strip_crop(x0, w, y0, y1, strip, "bottom")));
            parts.push(format!("[{name}c][{name}r]scale=w={}:h={}:flags=bilinear[{name}u]", sized("rw", w), sized("rh", half)));
            parts.push(format!("[{name}e][{name}q]scale=w={}:h=max(2\\,{}-{}):flags=bilinear[{name}l]", sized("rw", w), even("rh", h), even("rh", half)));
            parts.push(format!("[{name}u][{name}l]vstack[{name}p]"));
        } else {
            parts.push(format!("{source}split=3[{name}m][{name}s][{name}r]"));
            parts.push(format!("[{name}s]{}[{name}c]", strip_crop(x0, w, y0, y1, strip, look.direction)));
            parts.push(format!("[{name}c][{name}r]scale={size}:flags=bilinear[{name}p]"));
        }
        let feather = feathered(&format!("{name}p"), h, look.feather);
        let picture = if feather.is_empty() { format!("{name}p") } else { format!("{name}pf") };
        parts.extend(feather);
        let enables = enables(&group.windows);
        if enables.len() > 1 {
            parts.push(format!("[{picture}]split={}{}", enables.len(), (0..enables.len()).map(|chunk| format!("[{name}p{chunk}]")).collect::<String>()));
        }
        let mut base = format!("{name}m");
        for (chunk, enable) in enables.iter().enumerate() {
            let top = if enables.len() == 1 { picture.clone() } else { format!("{name}p{chunk}") };
            let label = if chunk == enables.len() - 1 {
                if index == last { String::new() } else { format!("[cv{}]", index + 1) }
            } else {
                format!("[{name}m{}]", chunk + 1)
            };
            parts.push(format!("[{base}][{top}]overlay=x={}:y={}{enable}{label}", even("W", x0), even("H", y0)));
            base = format!("{name}m{}", chunk + 1);
        }
    }
    parts.join(";")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn look(mode: &str) -> Look<'_> {
        Look { mode, direction: "top", strip_px: 4, color: "#112233", block: 24, feather: 0 }
    }

    fn area(y: f64, window: Option<(u64, u64)>) -> Area {
        Area { x: 0.1, y, w: 0.8, h: 0.1, window }
    }

    #[test]
    fn no_areas_no_filter() {
        assert_eq!(cover_filter(&[], &look("stretch")), None);
    }

    #[test]
    fn matches_the_engine_for_a_stretch_and_a_color() {
        // engine: cover_filtergraph([BlurRegion(0.1, 0.8, 0.8, 0.1)], CoverStyle(mode="stretch"))
        assert_eq!(cover_filter(&[area(0.8, None)], &look("stretch")).unwrap(),
            "split=3[cv0m][cv0s][cv0r];[cv0s]crop=w=max(2\\,trunc(iw*0.800000/2)*2):h=max(2\\,trunc(ih*0.003704/2)*2):x=trunc(iw*0.100000/2)*2:\
y=max(0\\,trunc(ih*0.800000/2)*2-oh)[cv0c];[cv0c][cv0r]scale=w=max(2\\,trunc(rw*0.800000/2)*2):h=max(2\\,trunc(rh*0.100000/2)*2):flags=bilinear[cv0p];\
[cv0m][cv0p]overlay=x=trunc(W*0.100000/2)*2:y=trunc(H*0.800000/2)*2");
        assert_eq!(cover_filter(&[area(0.8, Some((1000, 2500)))], &look("color")).unwrap(),
            "drawbox=x=trunc(iw*0.100000/2)*2:y=trunc(ih*0.800000/2)*2:w=max(2\\,trunc(iw*0.800000/2)*2):h=max(2\\,trunc(ih*0.100000/2)*2):\
color=0x112233:t=fill:enable=between(t\\,1.000\\,2.500)");
    }

    #[test]
    fn same_rectangles_share_one_chain_and_many_are_merged() {
        let graph = cover_filter(&[area(0.8, Some((0, 1000))), area(0.8, Some((500, 2000)))], &look("mosaic")).unwrap();
        assert!(graph.contains(":enable=between(t\\,0.000\\,2.000)"), "{graph}");
        assert_eq!(graph.matches("overlay").count(), 1);
        let many: Vec<Area> = (0..30).map(|index| Area { x: 0.01 * f64::from(index), y: 0.02 * f64::from(index), w: 0.05, h: 0.02, window: None }).collect();
        let graph = cover_filter(&many, &look("stretch")).unwrap();
        assert_eq!(graph.matches("overlay").count(), COVER_CHAINS);
        let blurred = cover_filter(&many, &look("blur")).unwrap();
        assert!(blurred.starts_with("split=3[blur_b0]"));
    }

    #[test]
    fn feathered_and_two_sided_chains_end_unlabeled() {
        let soft = Look { feather: 4, direction: "both", ..look("stretch") };
        let graph = cover_filter(&[area(0.1, None), area(0.8, None)], &soft).unwrap();
        assert!(graph.contains("vstack[cv0p]") && graph.contains("alphamerge[cv1pf]"), "{graph}");
        assert!(graph.contains("overlay=x=trunc(W*0.100000/2)*2:y=trunc(H*0.100000/2)*2[cv1]"));
        assert!(graph.ends_with("y=trunc(H*0.800000/2)*2"));
    }
}
