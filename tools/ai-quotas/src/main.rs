mod balancer;
mod cli;
mod history;
mod providers;
mod reader;
mod schema;
mod server;

fn main() {
    let args: Vec<String> = std::env::args_os()
        .skip(1)
        .map(|a| a.to_string_lossy().into_owned())
        .collect();
    std::process::exit(cli::run(args));
}
