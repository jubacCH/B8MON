fn main() {
    // Embed icon + version info into the Windows .exe
    #[cfg(target_os = "windows")]
    {
        match winresource::WindowsResource::new()
            .set_icon("icon.ico")
            .set("ProductName", "Nodeglow Agent")
            .set("FileDescription", "Nodeglow Monitoring Agent")
            .set("CompanyName", "Nodeglow")
            .set("LegalCopyright", "MIT License")
            .compile()
        {
            Ok(()) => {}
            Err(e) => {
                eprintln!("cargo:warning=Failed to embed Windows resources: {e}");
                // Non-fatal: build will succeed without icon/version info
            }
        }
    }
}
