use std::io::{Read, Write};
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
