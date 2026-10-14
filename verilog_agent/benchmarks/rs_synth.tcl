# Non-project OOC synthesis; argv: rtl top part outdir clock_ns.
if {[llength $argv] != 5} { error "expected rtl top part outdir clock_ns" }
lassign $argv rtl top part outdir clock_ns
file mkdir $outdir
read_verilog $rtl
set xdc [file join $outdir clock.xdc]
set constraints [open $xdc w]
puts $constraints "create_clock -name clk -period $clock_ns \[get_ports clk\]"
close $constraints
read_xdc $xdc
synth_design -top $top -part $part -mode out_of_context
report_utilization -file [file join $outdir utilization.rpt]
report_timing_summary -report_unconstrained -file [file join $outdir timing.rpt]
write_checkpoint -force [file join $outdir synthesized.dcp]
