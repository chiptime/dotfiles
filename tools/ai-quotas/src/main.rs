mod balancer;
mod cli;
mod costs;
mod history;
mod providers;
mod reader;
mod schema;
mod server;
mod status;

fn main() {
    let args: Vec<String> = std::env::args_os()
        .skip(1)
        .map(|a| a.to_string_lossy().into_owned())
        .collect();
    std::process::exit(cli::run(args));
}
