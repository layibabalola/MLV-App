extern "C" void processingGamutRgbToY(int, double out_rgb_to_Y[3])
{
    out_rgb_to_Y[0] = 0.2126729;
    out_rgb_to_Y[1] = 0.7151522;
    out_rgb_to_Y[2] = 0.0721750;
}

extern "C" void processingAgxMatrices(double out_forward[9], double out_inverse[9])
{
    for (int i = 0; i < 9; ++i) {
        out_forward[i] = (i % 4) == 0 ? 1.0 : 0.0;
        out_inverse[i] = out_forward[i];
    }
}
