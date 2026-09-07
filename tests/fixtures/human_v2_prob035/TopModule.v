// Source: runs/human-v2/gpt-5.6-terra/tasks/task_926407fc4b29/
//         candidates/cand_01/TopModule.v
module TopModule (
    input clk,
    input reset,
    output reg [3:0] q
);

    always @(posedge clk) begin
        if (reset) begin
            q <= 4'd1;
        end else if (q == 4'd10) begin
            q <= 4'd1;
        end else if ((q >= 4'd1) && (q <= 4'd9)) begin
            q <= q + 4'd1;
        end else begin
            q <= 4'd1;
        end
    end

endmodule
