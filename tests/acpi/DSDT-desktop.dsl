/*
 * Synthetic DSDT shaped like a typical Intel desktop (used by tests/run_tests.py).
 * Has what SSDTTime looks for on real hardware: PCI0 -> LPCB (0x001F0000) for SSDT-EC,
 * legacy Processor objects for SSDT-PLUG, an RTC (PNP0B00), a GPU for SSDT-PNLF, an XHC.
 * GitHub's runners are VMs without an LPC bridge, so their own tables cannot test this path.
 */
DefinitionBlock ("", "DSDT", 2, "HACKPE", "SYNTH", 0x00000001)
{
    Scope (\_PR)
    {
        Processor (CPU0, 0x01, 0x00001810, 0x06) {}
        Processor (CPU1, 0x02, 0x00001810, 0x06) {}
        Processor (CPU2, 0x03, 0x00001810, 0x06) {}
        Processor (CPU3, 0x04, 0x00001810, 0x06) {}
    }

    Scope (\_SB)
    {
        Device (PCI0)
        {
            Name (_HID, EisaId ("PNP0A08"))
            Name (_CID, EisaId ("PNP0A03"))
            Name (_ADR, Zero)
            Name (_UID, Zero)

            Device (LPCB)
            {
                Name (_ADR, 0x001F0000)

                Device (RTC)
                {
                    Name (_HID, EisaId ("PNP0B00"))
                    Name (_CRS, ResourceTemplate ()
                    {
                        IO (Decode16, 0x0070, 0x0070, 0x01, 0x08, )
                        IRQNoFlags () {8}
                    })
                }

                Device (TIMR)
                {
                    Name (_HID, EisaId ("PNP0100"))
                    Name (_CRS, ResourceTemplate ()
                    {
                        IO (Decode16, 0x0040, 0x0040, 0x01, 0x04, )
                        IRQNoFlags () {0}
                    })
                }
            }

            Device (GFX0)
            {
                Name (_ADR, 0x00020000)
            }

            Device (XHC)
            {
                Name (_ADR, 0x00140000)
            }
        }
    }
}
