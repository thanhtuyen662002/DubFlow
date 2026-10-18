use std::io::{BufRead, Read, Write};
use std::process::{Command, Stdio};
use std::time::Duration;
fn stall() -> ! {
    loop {
        std::thread::sleep(Duration::from_secs(1));
    }
}
fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mode = &args[1];
    if mode == "descendant" {
        stall();
    }
    let pid_file = &args[2];
    std::fs::write(pid_file, std::process::id().to_string()).unwrap();
    if mode.starts_with("service_") {
        service(mode, pid_file);
        return;
    }
    if mode == "ignore_stdin" {
        stall();
    }
    let mut request = String::new();
    std::io::stdin().read_to_string(&mut request).unwrap();
    assert!(request.ends_with('\n'));
    if mode == "tree" || mode == "orphaned_pipe" {
        let child = Command::new(std::env::current_exe().unwrap())
            .arg("descendant")
            .stdin(Stdio::null())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()
            .unwrap();
        std::fs::write(pid_file, format!("{},{}", std::process::id(), child.id())).unwrap();
        if mode == "orphaned_pipe" {
            println!("{{\"event\":\"source_scan_accepted\"}}");
            return;
        }
        stall();
    }
    match mode.as_str() {
        "partial" => {
            print!("{{\"event\":\"ready\"}}\n{{\"event\":");
            std::io::stdout().flush().unwrap();
            stall();
        }
        "silence" => stall(),
        "no_newline" => print!("{{\"event\":\"source_scan_accepted\"}}"),
        "chunks" => {
            for chunk in [
                "{\"event\":\"ready\"}\n",
                "{\"event\":",
                "\"source_scan_accepted\"}\n",
            ] {
                print!("{chunk}");
                std::io::stdout().flush().unwrap();
                std::thread::sleep(Duration::from_millis(10));
            }
        }
        "stderr" => {
            std::io::stderr()
                .write_all(&vec![b'x'; 512 * 1024])
                .unwrap();
            println!("{{\"event\":\"source_scan_accepted\"}}");
        }
        "stderr_flood" => {
            std::io::stderr()
                .write_all(&vec![b'x'; 2 * 1024 * 1024])
                .unwrap();
            stall();
        }
        "stdout_flood" => {
            std::io::stdout()
                .write_all(&vec![b'x'; 2 * 1024 * 1024])
                .unwrap();
            stall();
        }
        "exit_error" => {
            println!("{{\"event\":\"source_scan_accepted\"}}");
            std::process::exit(7);
        }
        "malformed" => println!("{{invalid-json"),
        "service_error" => println!("{{\"event\":\"error\",\"condition\":\"private-credential\"}}"),
        _ => panic!("unknown fixture"),
    }
}

fn service(mode: &str, pid_file: &str) {
    if mode == "service_admission_stall" {
        stall();
    }
    if mode == "service_slow" {
        std::thread::sleep(Duration::from_millis(200));
    }
    if mode == "service_wrong_ready" {
        println!("{{\"event\":\"ready\"}}");
        std::io::stdout().flush().unwrap();
        stall();
    }
    if mode == "service_stderr" {
        std::io::stderr()
            .write_all(&vec![b'x'; 512 * 1024])
            .unwrap();
    }
    println!("{{\"event\":\"source_ready\",\"schema_version\":1,\"single_writer\":true}}");
    std::io::stdout().flush().unwrap();
    match mode {
        "service_ignore_stdin" => stall(),
        "service_partial" => {
            print!("{{\"event\":\"source_");
            std::io::stdout().flush().unwrap();
            stall();
        }
        "service_stdout_flood" => {
            let _ = std::io::stdout().write_all(&vec![b'x'; 2 * 1024 * 1024]);
            stall();
        }
        "service_stderr_flood" => {
            let _ = std::io::stderr().write_all(&vec![b'x'; 2 * 1024 * 1024]);
            stall();
        }
        "service_events_flood" => {
            for _ in 0..1000 {
                println!("{{\"event\":\"source_status\"}}");
            }
            std::io::stdout().flush().unwrap();
            stall();
        }
        _ => (),
    }
    let mut child: Option<std::process::Child> = None;
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        let command = line
            .split("\"command\":\"")
            .nth(1)
            .unwrap()
            .split('"')
            .next()
            .unwrap();
        let mut log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(format!("{pid_file}.commands"))
            .unwrap();
        writeln!(log, "{command}").unwrap();
        match command {
            "start_video" => {
                let worker = Command::new(std::env::current_exe().unwrap())
                    .arg("descendant")
                    .stdin(Stdio::null())
                    .stdout(Stdio::inherit())
                    .stderr(Stdio::inherit())
                    .spawn()
                    .unwrap();
                std::fs::write(pid_file, format!("{},{}", std::process::id(), worker.id()))
                    .unwrap();
                child = Some(worker);
                println!("{{\"event\":\"source_preparing\",\"scan_id\":\"scan\"}}");
            }
            "cancel" => {
                if let Some(mut worker) = child.take() {
                    worker.kill().unwrap();
                    worker.wait().unwrap();
                }
                println!("{{\"event\":\"source_preparation_stopped\",\"scan_id\":\"scan\",\"cancelled\":true}}");
            }
            "shutdown" => {
                println!("{{\"event\":\"source_shutdown\"}}");
                std::io::stdout().flush().unwrap();
                return;
            }
            "status" => {
                println!("{{\"event\":\"source_download_progress\",\"scan_id\":\"other\",\"downloaded_bytes\":12}}");
                println!(
                    "{{\"event\":\"source_status\",\"scan_id\":\"scan\",\"status\":\"running\"}}"
                );
            }
            _ => println!("{{\"event\":\"source_error\",\"code\":\"SOURCE_REQUEST_REJECTED\"}}"),
        }
        std::io::stdout().flush().unwrap();
    }
    // EOF would destroy an active scan. The test must never get here after a
    // mere completed request; only explicit retirement may close this pipe.
    std::fs::write(format!("{pid_file}.eof"), b"closed").unwrap();
}
